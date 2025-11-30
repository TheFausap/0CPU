
# tests/test_tracing_core.py
import json
import math
from pathlib import Path
from tests.helpers_imports import mod

# Skip if observability not present
import pytest

has_observe = True
try:
    from cpu_sim.core.observe import TraceSink  # package form
except Exception:
    try:
        from observe import TraceSink  # flat form
    except Exception:
        has_observe = False

pytestmark = pytest.mark.skipif(not has_observe, reason="Observability (TraceSink) not found; apply CPU trace patches first.")

# Sample trig library and program (from user's example)
TRIG_LIB = """.libhdr
.constbase 1000
ONE:
q47  1.0
C3:
q47 -0.16666666666666666
C5:
q47  0.008333333333333333
C7:
q47 -0.0001984126984126984

D2:
q47 -0.5
D4:
q47  0.041666666666666664
D6:
q47 -0.001388888888888889

# X=104 X2=105 TMP=106
.libfn sin 0x01
.args 1
.returns r1
.clobbers r1,r2,r3
instr STORE_R1 104
instr LOAD_R2 104
instr LOAD_R3 104
instr MUL
instr ROUND
instr STORE_R1 105
instr LOAD_R1 @C7
instr STORE_R1 106
instr LOAD_R2 105
instr LOAD_R3 106
instr MUL
instr ROUND
instr LOAD_R2 @C5
instr ADD
instr STORE_R1 106
instr LOAD_R2 105
instr LOAD_R3 106
instr MUL
instr ROUND
instr LOAD_R2 @C3
instr ADD
instr STORE_R1 106
instr LOAD_R2 105
instr LOAD_R3 106
instr MUL
instr ROUND
instr LOAD_R2 @ONE
instr ADD
instr STORE_R1 106
instr LOAD_R2 104
instr LOAD_R3 106
instr MUL
instr ROUND
instr RET
.endl

.libfn cos 0x02
.args 1
.returns r1
.clobbers r1,r2,r3
instr STORE_R1 104
instr LOAD_R2 104
instr LOAD_R3 104
instr MUL
instr ROUND
instr STORE_R1 105
instr LOAD_R1 @D6
instr STORE_R1 106
instr LOAD_R2 105
instr LOAD_R3 106
instr MUL
instr ROUND
instr LOAD_R2 @D4
instr ADD
instr STORE_R1 106
instr LOAD_R2 105
instr LOAD_R3 106
instr MUL
instr ROUND
instr LOAD_R2 @D2
instr ADD
instr STORE_R1 106
instr LOAD_R2 105
instr LOAD_R3 106
instr MUL
instr ROUND
instr LOAD_R2 @ONE
instr ADD
instr RET
.endl
"""

PROGRAM = """.org 300
q47 0.5
.org 310
instr LOAD_R1 300
instr CALL LIBNAME sin
instr STORE_R1 330
instr LOAD_R3 330
instr WRITE_TAPE
instr LOAD_R1 300
instr CALL LIBNAME cos
instr STORE_R1 331
instr LOAD_R3 331
instr WRITE_TAPE
HALT
.start 310
"""


def build_library(tmp_path):
    lb = mod.tools_lib_builder.LibraryBuilder(TRIG_LIB)
    lb.parse()
    out = tmp_path / 'library.tape'
    lb.build(str(out))
    return out


def assemble_program(tmp_path):
    asm = mod.tools_assembler.MiniAssembler(PROGRAM)
    items, start = asm.assemble()
    scratch = mod.tape.TapeFile(str(tmp_path / 'scratchpad.tape'))
    # write items
    for it in items:
        scratch.write_bits(it.addr, it.bits48)
    return scratch, start


def test_trace_events_and_metrics(tmp_path):
    lib_path = build_library(tmp_path)
    scratch_dev, start_ip = assemble_program(tmp_path)
    library_dev = mod.tape.TapeFile(str(lib_path))
    cards_tape = mod.tape.TapeFile(str(tmp_path / 'cards.tape'))
    paper_tape = mod.tape.TapeFile(str(tmp_path / 'paper.tape'))
    cpu = mod.cpu.CPU(scratch_dev, library_dev, mod.tape.CardReader(cards_tape), mod.tape.PaperTape(paper_tape))

    # Wire trace sink (collector)
    try:
        from cpu_sim.core.observe import TraceSink as TS
    except Exception:
        from observe import TraceSink as TS
    buf = []
    cpu.set_trace_sink(TS(collector=buf))

    # Add simple anomaly rules if available
    try:
        from cpu_sim.tools.anomaly_rules import rule_deep_recursion, rule_high_latency
    except Exception:
        def rule_deep_recursion(ev, max_depth=128):
            return ["deep_recursion"] if (ev.get('stack_depth', 0) > max_depth) else []
        def rule_high_latency(ev, threshold_ms=50):
            lat = ev.get('latency_ms')
            return ["high_latency"] if (lat is not None and lat > threshold_ms) else []
    cpu.add_anomaly_rule(rule_deep_recursion)
    cpu.add_anomaly_rule(rule_high_latency)

    # Execute
    cpu._execute_block(scratch_dev, start_ip)

    # Assertions on trace buffer
    assert len(buf) > 0, "No trace events emitted"
    # There should be CALL, MUL/ROUND, WRITE_TAPE, RET, HALT events
    op_names = {ev.get('op_name') for ev in buf}
    assert 'CALL' in op_names
    assert 'MUL' in op_names and 'ROUND' in op_names
    assert 'WRITE_TAPE' in op_names
    assert 'RET' in op_names or 'HALT' in op_names

    # Check a CALL LIBNAME event has extra_words == 1 and pb_used == False
    call_events = [ev for ev in buf if ev.get('op_name') == 'CALL']
    assert any(ev.get('extra_words') == 1 for ev in call_events), "CALL LIBNAME should consume namehash immediate"
    assert all(ev.get('pb_used') in (None, False) for ev in call_events), "PB not used in this program"

    # Check device switching recorded
    assert any(ev.get('ctx_switch') for ev in call_events), "CALL should switch device"

    # Metrics integrity
    m = cpu.metrics
    assert m['instr_count'] >= len(buf)  # may equal; ensure updated
    assert m['max_stack_depth'] >= 1
    assert m['by_device'].get('library', 0) > 0
    assert m['by_opcode'].get('WRITE_TAPE', 0) == 2

    # Verify paper tape has two records (sin, cos)
    assert paper_tape.record_count() == 2
    s_bits = paper_tape.read_bits(0)
    c_bits = paper_tape.read_bits(1)
    assert isinstance(s_bits, int) and isinstance(c_bits, int)

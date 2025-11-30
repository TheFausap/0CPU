
# tests/test_tracing_cli.py
import json
from pathlib import Path
import pytest
from tests.helpers_imports import mod

pytestmark = pytest.mark.skipif(mod.cli is None, reason="cli.py not importable; place cli.py at project root.")

TRIG_LIB = """.libhdr
.constbase 1000
ONE:
q47 1.0
C3:
q47 -0.16666666666666666
C5:
q47 0.008333333333333333
C7:
q47 -0.0001984126984126984
D2:
q47 -0.5
D4:
q47 0.041666666666666664
D6:
q47 -0.001388888888888889
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
"""

PROGRAM = """.org 300
q47 0.5
.org 310
instr LOAD_R1 300
instr CALL LIBNAME sin
instr STORE_R1 330
instr LOAD_R3 330
instr WRITE_TAPE
HALT
.start 310
"""


def test_cli_run_tracing(tmp_path):
    # Write library source and build it via CLI
    lib_src = tmp_path / 'trig.lib'
    lib_src.write_text(TRIG_LIB, encoding='utf-8')
    lib_out = tmp_path / 'library.tape'
    assert mod.cli.main(["buildlib", str(lib_src), "-o", str(lib_out)]) == 0

    # Assemble program via CLI
    prog_src = tmp_path / 'program.asm'
    prog_src.write_text(PROGRAM, encoding='utf-8')
    scratch_out = tmp_path / 'scratchpad.tape'
    assert mod.cli.main(["assemble", str(prog_src), "-o", str(scratch_out)]) == 0

    trace_file = tmp_path / 'trace.jsonl'
    metrics_file = tmp_path / 'metrics.json'

    # Run with --trace-file and --trace-metrics
    rc = mod.cli.main([
        "run",
        "--scratch", str(scratch_out),
        "--library", str(lib_out),
        "--start", "310",
        "--trace-file", str(trace_file),
        "--trace-metrics", str(metrics_file),
        "--status",
    ])
    assert rc == 0

    # Verify trace file exists and has JSON lines
    assert trace_file.exists()
    lines = trace_file.read_text(encoding='utf-8').splitlines()
    assert len(lines) > 0
    first = json.loads(lines[0])
    assert 'op_name' in first and 'device' in first and 'r1' in first

    # Metrics file sanity
    assert metrics_file.exists()
    m = json.loads(metrics_file.read_text(encoding='utf-8'))
    assert m.get('instr_count', 0) >= len(lines)
    assert m.get('by_opcode', {}).get('WRITE_TAPE', 0) == 1


# run_lib_idx.py: CALL LIBIDX with PB extras; computes a + d + e
import sys, os as _os
sys.path.append(_os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))

from cpu_sim.tools.lib_builder import LibraryBuilder
from cpu_sim.tools.assembler import MiniAssembler
from cpu_sim.tools.cards_builder import CardsBuilder
from cpu_sim.tools.io_realism import TapeDevice
from cpu_sim.core.tape import TapeFile, CardReader, PaperTape
from cpu_sim.core.cpu import CPU
from cpu_sim.core.encoding import float_to_q47, q47_to_float

SCRATCH = 'scratchpad.tape'
LIB     = 'library.tape'
PAPER   = 'paper.tape'
CARDS   = 'cards.tape'
LISTING = 'cards_listing_idx.txt'
LIBASM  = _os.path.join(_os.path.dirname(__file__), 'lib_idx_demo.asm')

# Clean artifacts
for p in [SCRATCH, LIB, PAPER, CARDS, LISTING]:
    try: _os.remove(p)
    except FileNotFoundError: pass

# --- Build library with SumAE ---
txt = open(LIBASM, 'r').read()
lib = LibraryBuilder(txt)
lib.parse()
lib.build(LIB)

# --- Scratchpad program: CALL by index (0x02) with PB at @300 ---
prog_asm = """
.org 0
instr CALL LIBIDX 0x02 PB @300
instr STORE_R1 20
instr HALT
"""

items, start_addr = MiniAssembler(prog_asm).assemble()

# --- Prepare PB with 5 args: a,b,c,d,e ---
# We'll test a=0.2, b=0.1, c=0.3, d=0.05, e=0.15
scratch = TapeFile(SCRATCH)
scratch.write_word(300, 5)                       # PB[0] = count
scratch.write_word(301, float_to_q47(0.2))       # PB[1] = a -> r1
scratch.write_word(302, float_to_q47(0.1))       # PB[2] = b -> r2
scratch.write_word(303, float_to_q47(0.3))       # PB[3] = c -> r3
scratch.write_word(304, float_to_q47(0.05))      # PB[4] = d -> shadow[0]
scratch.write_word(305, float_to_q47(0.15))      # PB[5] = e -> shadow[1]

# --- Build cards deck ---
cards_tape = TapeFile(CARDS)
builder = CardsBuilder(cards_tape)
for it in items:
    builder.append_pair_store(it.bits48, it.addr)
builder.finalize_boot(start_addr)
builder.save_listing(LISTING)

# --- Devices (use TapeDevice for realism; latencies set to 0 for speed) ---
scratchpad = TapeDevice(SCRATCH, sequential_only=True, ms_per_word=0, start_stop_ms=0, error_rate=0.0)
library    = TapeDevice(LIB,     sequential_only=True, ms_per_word=0, start_stop_ms=0, error_rate=0.0)
paper_dev  = TapeDevice(PAPER,   sequential_only=True, ms_per_word=0, start_stop_ms=0, error_rate=0.0)
paper_tape = PaperTape(paper_dev)
card_reader = CardReader(cards_tape)

# --- Run ---
cpu = CPU(scratchpad, library, card_reader, paper_tape)
cpu.boot_from_cards()

out_q47 = scratchpad.read_word(20)
out_f    = q47_to_float(out_q47)

print({
    'start_addr': start_addr,
    'result_q47': out_q47,
    'result_float': out_f,  # expected ~ 0.2 + 0.05 + 0.15 = 0.4
    'cards_records': cards_tape.record_count(),
})


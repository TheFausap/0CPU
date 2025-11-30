
# run_lib.py: build library.tape, assemble program, build cards, boot and CALL by LIBNAME with PB
import sys
import os as _os

# Allow running directly from repo root
sys.path.append(_os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))

from cpu_sim.tools.lib_builder import LibraryBuilder
from cpu_sim.tools.assembler import MiniAssembler
from cpu_sim.tools.cards_builder import CardsBuilder
from cpu_sim.tools.io_realism import TapeDevice
from cpu_sim.core.tape import TapeFile, CardReader, PaperTape
from cpu_sim.core.cpu import CPU
from cpu_sim.core.encoding import float_to_q47, q47_to_float
from cpu_sim.core.opcodes import decode_op

# Filenames used by the demo
SCRATCH = 'scratchpad.tape'
LIB     = 'library.tape'
PAPER   = 'paper.tape'
CARDS   = 'cards.tape'
LISTING = 'cards_listing.txt'

# --- Clean previous artifacts ---
for p in [SCRATCH, LIB, PAPER, CARDS, LISTING]:
    try: _os.remove(p)
    except FileNotFoundError: pass

# --- Library source (self-contained) ---
lib_asm = """
; Library demo: FixMulRound
.libhdr
.libfn FixMulRound 0x01
.args 2
.returns r1
.clobbers r1

instr MUL
instr ROUND
instr RET
.endl
"""

# Build library.tape
lib_builder = LibraryBuilder(lib_asm)
lib_builder.parse()
lib_builder.build(LIB)

# --- Scratchpad program that calls by name with PB ---
prog_asm = """
.org 0
; Load constants (for reference; PB will also set them)
instr LOAD_R2 10
instr LOAD_R3 11

; Call library function by NAME with a Parameter Block at @200
instr CALL LIBNAME FixMulRound PB @200

; Store the result (r1) in scratchpad[12], then halt
instr STORE_R1 12
instr HALT

; Constants (Q47 fixed-point)
q47 @10 0.2
q47 @11 0.25
"""

# Assemble program
items, start_addr = MiniAssembler(prog_asm).assemble()

# Prepare PB at address 200:
# PB[0]=count, PB[1]=r1 (unused here), PB[2]=r2, PB[3]=r3
scratch = TapeFile(SCRATCH)
scratch.write_word(200, 3)                      # count
scratch.write_word(201, float_to_q47(0.0))      # r1 (unused)
scratch.write_word(202, float_to_q47(0.2))      # r2
scratch.write_word(203, float_to_q47(0.25))     # r3

# Build the boot cards deck (odd: data -> r1, even: execute)
cards_tape = TapeFile(CARDS)
builder = CardsBuilder(cards_tape)
for it in items:
    builder.append_pair_store(it.bits48, it.addr)
builder.finalize_boot(start_addr)
builder.save_listing(LISTING)

# Devices with I/O realism (latencies = 0 for fast demo)
scratchpad = TapeDevice(SCRATCH, sequential_only=True, ms_per_word=0, start_stop_ms=0, error_rate=0.0)
library    = TapeDevice(LIB,     sequential_only=True, ms_per_word=0, start_stop_ms=0, error_rate=0.0)
paper_dev  = TapeDevice(PAPER,   sequential_only=True, ms_per_word=0, start_stop_ms=0, error_rate=0.0)
paper_tape = PaperTape(paper_dev)

card_reader = CardReader(cards_tape)

# Run the CPU: boot from cards, then program CALLs library by name
cpu = CPU(scratchpad, library, card_reader, paper_tape)
cpu.boot_from_cards()

# Inspect the first two scratchpad instructions (should be CLEAR_R1 and HALT, per example flow)
ops = [(decode_op(scratchpad.read_bits(addr))) for addr in range(0, 2)]

# Read back the result
res_q47 = scratchpad.read_word(12)
res_float = q47_to_float(res_q47)

print({
    'start_addr'       : start_addr,
    'scratchpad_ops'   : ops,
    'r1_q47_int'       : cpu.r1,    # r1 after HALT
    'result_q47'       : res_q47,
    'result_float'     : res_float, # expected ~0.05 (0.2 * 0.25 rounded)
    'cards_records'    : cards_tape.record_count(),
})


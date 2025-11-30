
# run_boot.py: assemble -> build cards -> boot CPU with I/O realism
import sys
import os as _os
sys.path.append(_os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))

import os
from cpu_sim.tools.assembler import MiniAssembler
from cpu_sim.tools.cards_builder import CardsBuilder
from cpu_sim.core.tape import TapeFile, CardReader, PaperTape
from cpu_sim.core.cpu import CPU
from cpu_sim.core.opcodes import decode_op, pack_ff_operand, encode_instr
from cpu_sim.tools.io_realism import TapeDevice

SCRATCH = 'scratchpad.tape'
LIB     = 'library.tape'
PAPER   = 'paper.tape'
CARDS   = 'cards.tape'
LISTING = 'cards_listing.txt'
ASM     = os.path.join(os.path.dirname(__file__), 'boot_demo.asm')

# Clean files
for p in [SCRATCH, LIB, PAPER, CARDS, LISTING]:
    try: os.remove(p)
    except FileNotFoundError: pass

# Assemble
with open(ASM, 'r') as f: text = f.read()
assembler = MiniAssembler(text)
items, start_addr = assembler.assemble()

# Build cards (TapeFile is fine for cards)
cards_tape = TapeFile(CARDS)
builder = CardsBuilder(cards_tape)
for it in items:
    builder.append_pair_store(it.bits48, it.addr)
builder.finalize_boot(start_addr)
builder.save_listing(LISTING)

# Use TapeDevice for scratchpad & library
scratchpad = TapeDevice(SCRATCH, sequential_only=True, ms_per_word=0, start_stop_ms=0, error_rate=0.0)
library    = TapeDevice(LIB,     sequential_only=True, ms_per_word=0, start_stop_ms=0, error_rate=0.0)
paper_tape = PaperTape(TapeDevice(PAPER, sequential_only=True, ms_per_word=0, start_stop_ms=0))
card_reader = CardReader(cards_tape)  # CardReader reads from TapeFile

cpu = CPU(scratchpad, library, card_reader, paper_tape)
cpu.boot_from_cards()  # executes TXR .start

# Inspect program ops in scratchpad
ops = [(decode_op(scratchpad.read_bits(addr))) for addr in range(0, 2)]

# Device STATUS demo (scratchpad position -> r3)
cpu.execute_encoded(encode_instr('STATUS', 0))
print({
    'scratchpad_prog_ops': ops,
    'start_addr': start_addr,
    'status_pos': cpu.r3,
    'cards_records': cards_tape.record_count(),
})


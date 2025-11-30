
from cpu_sim.tools.assembler import MiniAssembler

asm = """
.org 0
instr CALL LIBIDX 0x01
instr CALL LIBNAME FixMulRound PB @200
instr CALL SCRATCH 42
instr CALL 1234
HALT
"""

items, start = MiniAssembler(asm).assemble()
for it in items:
    print(it)
print("start_addr:", start)


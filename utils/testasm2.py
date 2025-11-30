
from cpu_sim.tools.assembler import MiniAssembler

asm = """
.org 0
instr SLOAD_R2 0x100000  ; load extra PB arg d
instr ADD                 # r1 = r1 + r2
HALT                      ; end
"""

items, start = MiniAssembler(asm).assemble()
for it in items:
    print(it)
print("start_addr:", start)


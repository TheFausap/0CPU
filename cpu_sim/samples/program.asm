.org 0
.start loop
loop:
  instr LOAD_R1 10
  instr LOAD_R2 11
  instr ADD
  instr STORE_R1 200
  instr LOAD_R3 200
  instr WRITE_TAPE
  instr FF 0x00000002
  instr REWIND 0
  instr SKIP
  instr HALT

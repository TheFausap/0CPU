.org 1000
instr CLEAR_R1        ; y = 0
instr LOAD_R2 MONE    ; x = -1.0
instr CALL LIBNAME atan2
instr STORE_R1 2000   ; Store result
HALT

MONE:
q47 -1.0
.start 1000

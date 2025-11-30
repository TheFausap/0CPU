; --------------- Program to test tan, atan, atan2 (PB) ---------------
.org 300
q47 0.5                        ; y = 0.5
.org 301
q47 0.5                        ; x = 0.5

; PB block at 350:
.org 350
data 2                         ; PB[0] = count=2
.org 351
q47 0.5                        ; PB[1] = y
.org 352
q47 0.5                        ; PB[2] = x

.org 310
; tan(x)
instr LOAD_R1 301              ; r1 = x
instr CALL LIBNAME tan
instr STORE_R1 332
instr LOAD_R3 332
instr WRITE_TAPE

; atan(x)
instr LOAD_R1 301              ; r1 = x
instr CALL LIBNAME atan
instr STORE_R1 333
instr LOAD_R3 333
instr WRITE_TAPE

; atan2(y, x) via PB
instr CALL LIBNAME atan2 PB @350
instr STORE_R1 334
instr LOAD_R3 334
instr WRITE_TAPE

HALT
.start 310


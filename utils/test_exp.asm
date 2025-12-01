.org 100
; Test exp(1.0)
q47 1.0
; Test exp(0.5)
q47 0.5
; Test exp(-1.0)
q47 -1.0

.org 200
; exp(1.0)
instr LOAD_R1 100
instr CALL LIBNAME exp
instr STORE_R1 300
instr LOAD_R3 300
instr WRITE_TAPE

; exp(0.5)
instr LOAD_R1 101
instr CALL LIBNAME exp
instr STORE_R1 301
instr LOAD_R3 301
instr WRITE_TAPE

; exp(-1.0)
instr LOAD_R1 102
instr CALL LIBNAME exp
instr STORE_R1 302
instr LOAD_R3 302
instr WRITE_TAPE

instr HALT
.start 200

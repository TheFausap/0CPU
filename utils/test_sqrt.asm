.org 100
; Test sqrt(2.0)
q47 2.0
; Test sqrt(0.5)
q47 0.5
; Test inv_sqrt(2.0)
q47 2.0

.org 200
; sqrt(2.0)
instr LOAD_R1 100
instr CALL LIBNAME sqrt
instr STORE_R1 300
instr LOAD_R3 300
instr WRITE_TAPE

; sqrt(0.5)
instr LOAD_R1 101
instr CALL LIBNAME sqrt
instr STORE_R1 301
instr LOAD_R3 301
instr WRITE_TAPE

; inv_sqrt(2.0)
instr LOAD_R1 102
instr CALL LIBNAME inv_sqrt
instr STORE_R1 302
instr LOAD_R3 302
instr WRITE_TAPE

instr HALT
.start 200

.org 300
q47 0.5                           ; x = 0.5 rad
.org 310
instr LOAD_R1 300
;
instr CALL LIBNAME sin
instr STORE_R1 330
instr LOAD_R3 330
instr WRITE_TAPE                  ; write sin(x) to paper tape
;
instr LOAD_R1 300
;
instr CALL LIBNAME cos
instr STORE_R1 331
instr LOAD_R3 331
instr WRITE_TAPE                  ; write cos(x) to paper tape
;
HALT
.start 310

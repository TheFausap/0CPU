.org 10
instr JUMP INTEGRAND

.org 100
q47 0.0
q47 1.0
data 7 ; k=7 (128 steps)

.org 200
; Test 1: f(x) = x
; Set flag for f(x)=x (0)
instr CLEAR_R1
instr STORE_R1 300
instr LOAD_R1 100 ; a=0
instr LOAD_R2 101 ; b=1
instr LOAD_R3 102 ; k=7
instr CALL LIBNAME integrate
instr STORE_R1 301 ; Result 1
instr LOAD_R3 301
instr WRITE_TAPE

; Test 2: f(x) = x^2
; Set flag for f(x)=x^2 (1)
instr LOAD_R1 101 ; 1.0
instr STORE_R1 300
instr LOAD_R1 100 ; a=0
instr LOAD_R2 101 ; b=1
instr LOAD_R3 102 ; k=7
instr CALL LIBNAME integrate
instr STORE_R1 302 ; Result 2
instr LOAD_R3 302
instr WRITE_TAPE

instr HALT

INTEGRAND:
; Save x
instr STORE_R1 305

; Check flag at 300
instr LOAD_R1 300
instr SKIP_IF_ZERO
instr JUMP FUNC_SQUARE
instr JUMP FUNC_IDENTITY

FUNC_IDENTITY:
; Restore x
instr LOAD_R1 305
instr RET

FUNC_SQUARE:
; Calculate x^2
instr LOAD_R2 305
instr LOAD_R3 305
instr MUL
instr ROUND
instr RET

; Correct logic:
; Entry: r1 has x.
; Save x to 305.
; Check flag.
; Restore x.
; Compute.

.start 200

.org 100
bits 00000000007B ; 123
bits FFFF00000000 ; Large negative number (-4294967296)
; -42 = -0x2A = ...FFD6
bits FFFFFFFFFFD6 ; -42
bits 000000000000 ; 0
bits 000000000003 ; 3

.org 200
; Test 1: print_u_dec(123) -> "123"
instr LOAD_R3 100 ; 123
instr CALL LIBNAME print_u_dec
instr LOAD_R3 NEWLINE
instr WRITE_TAPE

; Test 2: print_dec(large negative number) -> "-4294967296"
instr LOAD_R3 101 ; -4294967296
instr CALL LIBNAME print_dec
instr LOAD_R3 NEWLINE
instr WRITE_TAPE

; Test 3: print_dec(-42) -> "-42"
instr LOAD_R3 102 ; -42
instr CALL LIBNAME print_dec
instr LOAD_R3 NEWLINE
instr WRITE_TAPE

; Test 4: print_dec(0) -> "0"
instr LOAD_R3 103 ; 0
instr CALL LIBNAME print_dec
instr LOAD_R3 NEWLINE
instr WRITE_TAPE

; Test 4: print_dec(3) -> "3"
instr LOAD_R3 104 ; 3
instr CALL LIBNAME print_dec
instr LOAD_R3 NEWLINE
instr WRITE_TAPE

instr HALT

NEWLINE:
data 10 ; \n

.start 200

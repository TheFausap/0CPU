.org 100
bits 000000000001 ; Pattern 1
bits 800000000000 ; Pattern 2 (High bit set)
bits 123456789ABC ; Pattern 3 (Random)

.org 200
; Test 1: Rotate Left 1
instr LOAD_R1 100 ; 1
instr ROTATE_LEFT 1
instr STORE_R1 300
instr LOAD_R3 300
instr WRITE_TAPE ; Expect 2

; Test 2: Rotate Right 1
instr LOAD_R1 300 ; 2
instr ROTATE_RIGHT 1
instr STORE_R1 301
instr LOAD_R3 301
instr WRITE_TAPE ; Expect 1

; Test 3: Rotate Left 48 (Identity)
instr LOAD_R1 102 ; Pattern 3
instr ROTATE_LEFT 48
instr STORE_R1 302
instr LOAD_R3 302
instr WRITE_TAPE ; Expect Pattern 3

; Test 4: Rotate Right 48 (Identity)
instr LOAD_R1 102 ; Pattern 3
instr ROTATE_RIGHT 48
instr STORE_R1 303
instr LOAD_R3 303
instr WRITE_TAPE ; Expect Pattern 3

; Test 5: Rotate Left Wrap (High bit -> Low bit)
instr LOAD_R1 101 ; High bit set
instr ROTATE_LEFT 1
instr STORE_R1 304
instr LOAD_R3 304
instr WRITE_TAPE ; Expect 1

; Test 6: Rotate Right Wrap (Low bit -> High bit)
instr LOAD_R1 100 ; 1
instr ROTATE_RIGHT 1
instr STORE_R1 305
instr LOAD_R3 305
instr WRITE_TAPE ; Expect High bit set

instr HALT
.start 200

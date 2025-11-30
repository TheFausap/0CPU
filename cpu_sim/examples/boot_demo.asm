
; Example mini-assembly for boot deck
.org 0
instr CLEAR_R1
instr HALT

; Place a Q47 constant at address 10
q47 @10 0.125
; Place a raw integer constant at address 11
data @11 0x2A

.start 0

.libhdr

; Existing function
.libfn SumAE 0x02
.args 5
.returns r1
.clobbers r1,r2
instr SLOAD_R2 0x100000   ; r2 <- d
instr ADD                 ; r1 = a + d
instr SLOAD_R2 0x100001   ; r2 <- e
instr ADD                 ; r1 = a + d + e
instr RET
.endl

; New function: FixAdd
.libfn FixAdd 0x03
.args 2
.returns r1
.clobbers r1
instr ADD
instr RET
.endl

; New function: FixDiv
.libfn FixDiv 0x04
.args 2
.returns r1
.clobbers r1
instr DIV
instr RET
.endl


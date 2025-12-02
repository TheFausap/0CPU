# Explanation of `test_integrate.asm`

This document explains the structure and logic of `utils/test_integrate.asm`, which verifies the `integrate` function in `math.libasm`.

## 1. Callback Hook Setup
```asm
.org 10
instr JUMP INTEGRAND
```
*   **Purpose**: The `integrate` function in the library is designed to call `CALL SCRATCH 10` whenever it needs to evaluate the function $f(x)$.
*   **Action**: We place a `JUMP` instruction at address `10` in the scratchpad memory. This redirects execution to our custom `INTEGRAND` label, where we define the function logic.

## 2. Data Section
```asm
.org 100
q47 0.0
q47 1.0
data 7 ; k=7 (128 steps)
```
*   **Purpose**: Defines constants used in the tests.
*   **Address 100**: `0.0` (Lower bound $a$).
*   **Address 101**: `1.0` (Upper bound $b$).
*   **Address 102**: `7` (Integer $k$). The integration will use $2^7 = 128$ steps.

## 3. Main Program
The main execution starts at address `200`.

### Test 1: Integrate $f(x) = x$
```asm
; Set flag for f(x)=x (0)
instr CLEAR_R1
instr STORE_R1 300
```
*   **Flag**: We use address `300` as a flag to tell our `INTEGRAND` function which formula to use. `0` means $f(x) = x$.

```asm
instr LOAD_R1 100 ; a=0
instr LOAD_R2 101 ; b=1
instr LOAD_R3 102 ; k=7
instr CALL LIBNAME integrate
```
*   **Call**: We load the arguments ($a=0, b=1, k=7$) and call the `integrate` function from the library.

```asm
instr STORE_R1 301 ; Result 1
instr LOAD_R3 301
instr WRITE_TAPE
```
*   **Output**: The result (expected `0.5`) is stored in `301` and written to the paper tape.

### Test 2: Integrate $f(x) = x^2$
```asm
; Set flag for f(x)=x^2 (1)
instr LOAD_R1 101 ; 1.0
instr STORE_R1 300
```
*   **Flag**: We set the flag at `300` to `1.0` (non-zero) to indicate we want to calculate $x^2$.

```asm
instr LOAD_R1 100 ; a=0
instr LOAD_R2 101 ; b=1
instr LOAD_R3 102 ; k=7
instr CALL LIBNAME integrate
instr STORE_R1 302 ; Result 2
instr LOAD_R3 302
instr WRITE_TAPE
```
*   **Call & Output**: Same as Test 1, but now the integrand will compute $x^2$. The result (expected $\approx 0.333$) is stored in `302`.

## 4. Integrand Function
This is the function called by `integrate` for every step.

```asm
INTEGRAND:
; Save x
instr STORE_R1 305
```
*   **Input**: The `integrate` function passes the current value of $x$ in register `r1`.
*   **Save**: We immediately save `x` to a temporary address (`305`) because we need to use `r1` to check the flag.

```asm
; Check flag at 300
instr LOAD_R1 300
instr SKIP_IF_ZERO
instr JUMP FUNC_SQUARE
instr JUMP FUNC_IDENTITY
```
*   **Dispatch**: We check the flag at `300`.
    *   If `0`: Jump to `FUNC_IDENTITY` ($f(x)=x$).
    *   If `!= 0`: Jump to `FUNC_SQUARE` ($f(x)=x^2$).

### Function Logic
**Identity ($f(x)=x$):**
```asm
FUNC_IDENTITY:
; Restore x
instr LOAD_R1 305
instr RET
```
*   Simply restores `x` into `r1` and returns.

**Square ($f(x)=x^2$):**
```asm
FUNC_SQUARE:
; Calculate x^2
instr LOAD_R2 305
instr LOAD_R3 305
instr MUL
instr ROUND
instr RET
```
*   Computes $x \times x$ and returns the result in `r1`.

## Summary
1.  **Setup**: Redirects `CALL SCRATCH 10` to `INTEGRAND`.
2.  **Test 1**: Sets flag=0, calls `integrate`, expects 0.5.
3.  **Test 2**: Sets flag=1, calls `integrate`, expects 0.333.
4.  **Integrand**: Checks flag and computes either $x$ or $x^2$.

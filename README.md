
# 48-bit Tape CPU Simulator (Q47) — Project README

This repository contains a 48‑bit, fixed‑point (Q47) **tape CPU simulator** with:

- A core CPU that executes 48‑bit encoded instructions over **scratchpad** and **library** tapes.
- An instruction set for ALU, control‑flow, I/O, and device realism operations.
- Two assemblers:
  - **MiniAssembler** for program sources (supports `CALL LIBNAME`, `PB` arguments, labels, directives).
  - **LibraryBuilder** for building a **library tape** with header, TOC, function bodies, and a constant pool.
- A **CLI/monitor** to build, assemble, run, and observe programs.
- **Observability** (per‑instruction JSONL tracing + metrics) and a small analysis script.

> **Numeric model**: All registers are signed **Q47** fixed point (48‑bit two’s‑complement with 47 fractional bits), representing real values in **[−1.0, 1.0)**.

---

## Table of contents

- [Project layout](#project-layout)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Building a library](#building-a-library)
- [Assembling and running a program](#assembling-and-running-a-program)
- [Device realism](#device-realism)
- [Observability & tracing](#observability--tracing)
- [Instruction set overview](#instruction-set-overview)
- [Fixed‑point details (Q47)](#fixed-point-details-q47)
- [PB (parameter block) calling convention](#pb-parameter-block-calling-convention)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [License](#license)

---

## Project layout

```
OCPU/
├─ cpu_sim/
│  ├─ __init__.py
│  ├─ core/
│  │  ├─ __init__.py
│  │  ├─ encoding.py        # 48‑bit two’s‑complement, bytes<->word, Q47 helpers
│  │  ├─ opcodes.py         # opcode table + instruction packing/decoding
│  │  ├─ tape.py            # TapeFile/CardReader/PaperTape storage
│  │  ├─ cpu.py             # CPU executor (ALU, CALL/RET, TXR, realism ops)
│  │  └─ observe.py         # TraceSink + helpers (new)
│  └─ tools/
│     ├─ __init__.py
│     ├─ io_realism.py      # TapeDevice with latency/errors/sequential behavior
│     ├─ assembler.py       # MiniAssembler (program)
│     ├─ lib_builder.py     # LibraryBuilder (library.tape)
│     ├─ cards_builder.py   # Cards builder for boot (single‑word ops only)
│     ├─ anomaly_rules.py   # Sample anomaly rules (new)
│     └─ trace_analyze.py   # Simple JSONL analysis (new)
├─ cli.py                   # CLI and interactive monitor
├─ tests/                   # Tracing test suite
│  ├─ helpers_imports.py
│  ├─ test_tracing_core.py
│  ├─ test_tracing_cli.py
│  └─ conftest.py
└─ pytest.ini
```

---

## Prerequisites

- **Python** ≥ 3.10 (tested on macOS/Homebrew Python 3.14 as well).
- Recommended: a virtual environment
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```
- Install runtime deps (none required for core). For tests:
  ```bash
  pip install pytest
  ```
- Optional: **editable install** to unify imports across tools/tests:
  ```bash
  # minimal pyproject.toml is sufficient (see notes in cli/README if provided)
  pip install -e .
  ```

---

## Quick start

1) **Build the library** (example trig library):

```bash
python cli.py buildlib trig.lib -o library.tape
```

2) **Assemble a program**:

```bash
python cli.py assemble program.asm -o scratchpad.tape --listing scratchpad.lst
```

3) **Run** (direct start):

```bash
python cli.py run --scratch scratchpad.tape --library library.tape --start 310 --status
```

4) **Interactive monitor**:

```bash
python cli.py monitor --scratch scratchpad.tape --library library.tape --blinklights
# commands inside monitor:
#   start <addr>   step [n]   run [max_steps]
#   regs           lights     device scratch|library
#   disasm <addr> [count]
#   libtoc         read <addr> [count]   write <addr> <signed>
#   rewind [scratch|library]   ff <dev> <count>   status [dev]
#   paper          paperf (float decode of Q47)
```

> **Card boot**: The cards builder emits **only single‑word instructions** (`STORE_R1`, `TXR`), which are safe for `boot_from_cards`. Multi‑word CALL forms must **not** be used during card boot.

---

## Building a library

Library sources are assembled by **LibraryBuilder** to `library.tape`, which contains:

- Header: `LIB_MAGIC`, `VERSION`, `ENTRY_COUNT`, `TOC_START`
- TOC entries: **4 words** per function → `[ID, NAMEHASH, START, LENGTH]`
- Functions: 3‑word function header (magic `FNHD`, metadata, reserved), then **encoded instruction words**
- Constant pool: global labels at absolute addresses, written by random access

**Directives supported (library)**:

- `.libhdr` — start of library source
- `.constbase <addr>` — set base address for constants
- `.org <addr>` — set absolute address for subsequent globals
- `label:` — define a global label at current address (outside `.libfn` blocks)
- `data <int>` — write signed Q47 value (encoded to 48‑bit two’s‑complement)
- `q47 <float>` — write fixed‑point constant
- `bits <hex48>` — write raw 48‑bit word (12 hex digits)
- `.libfn <name> <id>` — begin function definition
- `.args <n>` — number of args
- `.returns r1|r1:r2` — return mode
- `.clobbers r1[,r2[,r3]]` — clobber bitmap
- `instr <MNEMONIC> [OPERAND|@global_label]` — instruction inside function body
- `.endl` — end function

> **Note**: Within **library function bodies**, **do not** use `CALL LIBNAME`/`PB` syntax (these are **program‑assembler features**). If you need `atan(y/x)` inside a function, implement it directly with the Horner series (as in the trig functions).

**Magic alignment**:
- `LIB_MAGIC` must be **`0x4C49424844`** in both `cpu_sim/core/cpu.py` and `cpu_sim/tools/lib_builder.py`.

---

## Assembling and running a program

**Program assembler (MiniAssembler) features**:

- Labels and two‑pass resolution
- Directives: `.org`, `.start`, `data`, `q47`, `bits`, `instr`
- CALL forms:
  - `CALL SCRATCH <abs>`
  - `CALL LIBADDR <abs>`
  - `CALL LIBIDX <index>`
  - `CALL LIBNAME <name>` → emits an **extra 48‑bit word** with the **namehash**
  - Optional `PB @<addr>` → emits another **extra 48‑bit immediate** with the PB address

**PB (Parameter Block)**:
- At `PB_ADDR`: `PB[0]=count`, `PB[1]=arg1` → **preloaded into `r1`**, `PB[2]=arg2` → **preloaded into `r2`**, `PB[3]=arg3` → **preloaded into `r3`**
- Extra args beyond 3 are copied into a scratchpad shadow window at `CPU.PB_SHADOW_BASE`.

**Example (program)**:

```asm
.org 300
q47 0.5                        ; y
.org 301
q47 0.5                        ; x

; PB block at 350
.org 350
data 2                         ; PB[0] = count
.org 351
q47 0.5                        ; PB[1] = y
.org 352
q47 0.5                        ; PB[2] = x

.org 310
; atan2(y,x)
instr CALL LIBNAME atan2 PB @350
instr STORE_R1 334
instr LOAD_R3 334
instr WRITE_TAPE

HALT
.start 310
```

Run:
```bash
python cli.py assemble program.asm -o scratchpad.tape
python cli.py run --scratch scratchpad.tape --library library.tape --start 310 --status
```

---

## Device realism

Wrap scratchpad/library with `TapeDevice` for **latency**, **start/stop overhead**, **error injection with retries**, and **sequential behavior**:

```bash
python cli.py run --scratch scratchpad.tape --library library.tape --start 310 \
  --realistic --sequential-only --latency 12 --start-stop-ms 50 --error-rate 0.01 --status
```

Realism opcodes in programs:
- `REWIND` (operand: device code 0=scratchpad, 1=library, 2=cards)
- `FF` (operand packs device in top 12 bits, count in low 24)
- `STATUS` (operand: device code; result → `r3`)

> Ensure devices implement `rewind()`, `fast_forward(n)`, and `get_position()`; `TapeDevice` does, `TapeFile` provides basic storage.

---

## Observability & tracing

Enable **per‑instruction JSONL trace** and **metrics** from the CLI:

```bash
python cli.py run --scratch scratchpad.tape --library library.tape --start 310 \
  --trace-file trace.jsonl --trace-metrics metrics.json --status
```

Each trace event includes:
```json
{
  "ts": 1732927145.732,
  "ip": 512,
  "device": "library",
  "op_code": 0x009,
  "op_name": "ADD",
  "operand_raw": 0x000000123,
  "operand_dec": 291,
  "r1": 67473145400784,
  "r2": 35184372088832,
  "r3": 123508751987869,
  "stack_depth": 1,
  "ctx_switch": false,
  "extra_words": 0,
  "pb_used": false,
  "dev_pos": 1024,
  "error": null,
  "latency_ms": 12,
  "start_stop_ms": 50,
  "seq_only": true,
  "anomalies": []
}
```

Analyze a trace:
```bash
python -m cpu_sim.tools.trace_analyze trace.jsonl
```

Register anomaly rules programmatically:
```python
from cpu_sim.tools.anomaly_rules import rule_deep_recursion, rule_high_latency
cpu.add_anomaly_rule(rule_deep_recursion)
cpu.add_anomaly_rule(rule_high_latency)
```

Monitor also supports `--trace-file`:
```bash
python cli.py monitor --scratch scratchpad.tape --library library.tape --trace-file monitor_trace.jsonl
```

---

## Instruction set overview

**ALU**
- `ADD` (r1 := clamp(r1 + r2))
- `NEG` (r1 := clamp(-r1))
- `MUL` (r2 * r3 → r1:r2 as Q94 across the pair)
- `DIV` (r1 / r2 → r1; numerator scaled by Q47; divide‑by‑zero saturates to ±MAX)
- `ROUND` (Q94 in r1:r2 → Q47 in r1; nearest, away from zero)
- `AND`, `OR`, `XOR` (bitwise on 48‑bit two’s‑complement bit patterns)
- `SHIFT_LEFT`, `SHIFT_RIGHT` (logical shifts across r1:r2 as 96‑bit)

**Control & flow**
- `SKIP`, `SKIP_IF_ZERO`, `SKIP_IF_NONZERO`
- `TXR` (transfer/execute scratchpad block)
- `CALL` (modes: SCRATCH_ABS, LIB_ABS, LIB_IDX, LIB_NAME; optional `PB`)
- `RET`
- `HALT`

**Memory & I/O**
- `LOAD_R1`, `LOAD_R2`, `LOAD_R3` (from current device)
- `STORE_R1`, `STORE_R3` (to current device)
- `WRITE_TAPE` (append r3 to paper tape)
- `READ_CARD` (read next card into r3)

**Cross‑device**
- `SLOAD_R1`, `SLOAD_R2`, `SLOAD_R3` (always from scratchpad)

**Realism ops**
- `REWIND`, `FF`, `STATUS`

**Packing helpers** (see `opcodes.py`): ensure **bitwise OR (`|`)** between fields:
- `pack_call_operand(mode, flags, value)` → `((mode << 32) | (flags << 28) | value28)`
- `encode_instr(op_name, operand)` → `((op << OPR_BITS) | to_tc36(operand)) & WORD_MASK`
- `pack_ff_operand(dev, count)` → `((dev << 24) | count)`

---

## Fixed‑point details (Q47)

- Registers hold signed Q47 integers (48‑bit two’s‑complement; 47 fractional bits).
- Range: **[−2^47, 2^47−1]** → scaled to **[−1.0, 1.0)** in real values.
- `MUL`: produces Q94 across r1:r2; `ROUND` collapses to Q47.
- `DIV`: `r1 ← (r1 << FRAC_BITS) // r2`, clamped; divide‑by‑zero → saturate to ±MAX.
- Rounding policy: **nearest, away from zero** (adds/subtracts **0.5** in Q47 before downshift).

**Implications**:
- Angles ≥ 1 rad saturate; quadrant adjustments in `atan2` use ±1.0 as a saturated proxy for ±π (given the Q47 range).

---

## PB (Parameter Block) calling convention

- `PB[0]`: count
- `PB[1]`: arg1 → `r1`
- `PB[2]`: arg2 → `r2`
- `PB[3]`: arg3 → `r3`
- Extras beyond 3 copied to shadow at `CPU.PB_SHADOW_BASE`.

**Program CALL** example:
```asm
instr CALL LIBNAME foo PB @350
```
- Emits 2 extra words after the CALL: **namehash** and **PB address** (order depends on CALL mode then PB flag).
- CPU preloads registers and switches device context to **library** for the call target.

---

## Tests

A small test suite verifies tracing and CLI wiring.

Run:
```bash
pytest -q
```
If import issues occur, ensure you run from the project root (`OCPU/`), and the package markers exist (`__init__.py` in `cpu_sim/*`). The test suite includes `tests/conftest.py` that inserts the project root into `sys.path`.

**What’s covered**:
- Build library, assemble program, run CPU with in‑memory `TraceSink` → assert CALL/WRITE_TAPE events, metrics.
- CLI path (`buildlib`, `assemble`, `run --trace-file --trace-metrics`) → assert trace/metrics files created and sane.

---

## Troubleshooting

- **Invalid library magic header**: Ensure `LIB_MAGIC` is **`0x4C49424844`** in both CPU and builder; rebuild the library.
- **Wrong trig outputs**:
  - Verify function bodies perform full Horner pipeline: compute `t`, then `u = 1 + t*x^2`, then final `x*u`, storing/loading from TMP where needed.
  - Ensure ALU `OR` uses bitwise `|` in `cpu.py` (not line continuation `\`).
- **CALL LIBNAME fails**: Program assembler only. Library function bodies must not use `CALL LIBNAME`/`PB`.
- **Card boot**: Only single‑word ops allowed. Do not place CALL with extra immediates on cards.
- **EOF behavior**: `read_bits()` should return **`None`** when out‑of‑range; the CPU relies on this to detect missing immediates.
- **Import errors**: Run `pytest` from project root; consider `pip install -e .` for a unified package import.

---

## Roadmap

- Arithmetic right shift across r1:r2 (sign‑extend), separate from logical shifts.
- Additional math functions using rational (Padé/minimax) approximations for improved accuracy.
- Trace rotation & limits (`--trace-max-lines`), richer anomaly rules.
- CI workflow (GitHub Actions): lint, tests, artifact upload of traces on failure.

---

## License

Add your preferred license (MIT/Apache‑2.0) here.


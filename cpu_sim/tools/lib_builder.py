# lib_builder.py: build library.tape from library assembly (inline comment safe)
from typing import List, Tuple
import os
import sys

from ..core.encoding import (
    WORD_MASK,
    BYTE_PER_WORD,
    to_twos_complement,
    float_to_q47,
    fnv1a_hash_48,
    word_to_bytes,
)
from ..core.opcodes import encode_instr

LIB_MAGIC   = 0x4C4942484400  # 'LIBHD' truncated to 48-bit
VERSION     = 0x000000000001
FNHDR_MAGIC = 0x464E4844      # 'FNHD'
ABI_VER     = 0x001           # 12-bit ABI


class LibFunction:
    def __init__(self, name: str, fn_id: int):
        self.name      = name
        self.fn_id     = fn_id
        self.namehash  = fnv1a_hash_48(name)
        self.args      = 0
        self.returns   = 0  # 0=r1, 1=r1:r2
        self.clobbers  = 0  # bit0=r1, bit1=r2, bit2=r3
        self.body: List[int] = []  # encoded instruction bits
        self.local_labels: dict[str, int] = {} # label -> body index
        self.start     = 0
        self.length    = 0


class LibraryBuilder:
    """
    Library assembler → library.tape

    Directives (line-based, inline comments via ';' or '#'):
      .libhdr
      .constbase <addr>         # NEW: set absolute addr for constant pool (outside .libfn)
      .org <addr>               # NEW: set absolute addr for globals (outside .libfn)
      .libfn <name> <id>        # begin function
      .args <n>
      .returns r1 | r1:r2
      .clobbers r1[,r2[,r3]]
      instr <MNEMONIC> [OPERAND or @label]
      .endl                     # end function

      # Global constants (outside .libfn):
      label:                    # NEW: define a global label at current addr
      data [<int>]              # signed Q47, encoded to 48-bit two's complement
      q47  <float>              # encode Q47 fixed-point
      bits <hex48>              # raw 48-bit word (12 hex digits)

    Behavior:
      - Functions are written sequentially after header & TOC.
      - Globals (constant pool) are written by random access to their absolute addresses.
      - Instruction operands inside functions may reference global labels; resolved during build.
      - Overlap safety checks warn/error if globals collide with header/TOC or function region.
    """

    def __init__(self, text: str):
        self.text = text
        self.functions: List[LibFunction] = []
        self.current: LibFunction | None = None

        # Global constant pool
        self.globals: List[Tuple[int, int]] = []          # (addr, bits48)
        self.glob_loc: int | None = None                  # current absolute addr for globals
        self.global_labels: dict[str, int] = {}           # label -> absolute addr

        # Deferred label operands inside function bodies
        # Each entry: (fn_obj, body_index, mnemonic, label_name)
        self.pending_instr_labels: List[Tuple[LibFunction, int, str, str]] = []

    # ---------- helpers ----------
    @staticmethod
    def _strip_inline_comments(line: str) -> str:
        if not line:
            return line
        cut_positions = []
        for marker in (';', '#'):
            p = line.find(marker)
            if p != -1:
                cut_positions.append(p)
        if cut_positions:
            line = line[:min(cut_positions)]
        return line

    @staticmethod
    def _parse_int(tok: str) -> int:
        tok = tok.strip()
        if tok.lower().startswith('0x'):
            return int(tok, 16)
        return int(tok)

    @staticmethod
    def _parse_hex48(tok: str) -> int:
        h = tok.strip().lower()
        if h.startswith("0x"): h = h[2:]
        if len(h) > 12:
            raise ValueError("hex48 too long; expected up to 12 hex digits")
        h = h.zfill(12)
        return int(h, 16) & WORD_MASK

    def _emit_instr(self, mnem: str, operand: int = 0):
        if self.current is None:
            raise ValueError("instr outside of a .libfn block")
        self.current.body.append(encode_instr(mnem, operand))

    # ---------- parse ----------
    def parse(self):
        for lineno, raw in enumerate(self.text.splitlines(), start=1):
            line = self._strip_inline_comments(raw).strip()
            if not line:
                continue

            # Label definition
            if line.endswith(":") and (" " not in line):
                label = line[:-1].strip()
                if not label:
                    raise ValueError(f"[line {lineno}] Empty label name")
                
                if self.current is not None:
                    # Local label inside function
                    if label in self.current.local_labels:
                        raise ValueError(f"[line {lineno}] Duplicate local label: {label}")
                    # Store relative offset (index in body)
                    self.current.local_labels[label] = len(self.current.body)
                else:
                    # Global label
                    if self.glob_loc is None:
                        raise ValueError(f"[line {lineno}] Label '{label}' requires prior .constbase/.org")
                    if label in self.global_labels:
                        raise ValueError(f"[line {lineno}] Duplicate global label: {label}")
                    self.global_labels[label] = self.glob_loc
                continue

            toks = line.split()
            head = toks[0]

            # Header
            if head == '.libhdr':
                continue

            # NEW: .constbase
            if head == '.constbase':
                if len(toks) < 2:
                    raise ValueError(f"[line {lineno}] .constbase requires <addr>")
                addr = self._parse_int(toks[1])
                if self.current is not None:
                    raise ValueError(f"[line {lineno}] .constbase not allowed inside .libfn")
                self.glob_loc = addr
                continue

            # NEW: global .org
            if head == '.org':
                if len(toks) < 2:
                    raise ValueError(f"[line {lineno}] .org requires <addr>")
                addr = self._parse_int(toks[1])
                if self.current is not None:
                    raise ValueError(f"[line {lineno}] .org not allowed inside .libfn (use only for globals)")
                self.glob_loc = addr
                continue

            # begin function
            if head == '.libfn':
                if len(toks) < 3:
                    raise ValueError(f"[line {lineno}] .libfn requires <name> <id>")
                name  = toks[1]
                fn_id = self._parse_int(toks[2])
                self.current = LibFunction(name, fn_id)
                self.functions.append(self.current)
                continue

            # end function
            if head == '.endl':
                self.current = None
                continue

            # metadata (inside function)
            if head == '.args':
                if self.current is None:
                    raise ValueError(f"[line {lineno}] .args outside of a .libfn block")
                if len(toks) < 2:
                    raise ValueError(f"[line {lineno}] .args requires a number")
                self.current.args = self._parse_int(toks[1])
                continue

            if head == '.returns':
                if self.current is None:
                    raise ValueError(f"[line {lineno}] .returns outside of a .libfn block")
                if len(toks) < 2:
                    raise ValueError(f"[line {lineno}] .returns requires r1 or r1:r2")
                mode = toks[1].lower()
                self.current.returns = 1 if mode == 'r1:r2' else 0
                continue

            if head == '.clobbers':
                if self.current is None:
                    raise ValueError(f"[line {lineno}] .clobbers outside of a .libfn block")
                if len(toks) < 2:
                    raise ValueError(f"[line {lineno}] .clobbers requires a comma-separated list")
                bm = 0
                for r in toks[1].split(','):
                    rr = r.strip().lower()
                    if rr == 'r1':
                        bm |= 1
                    elif rr == 'r2':
                        bm |= 2
                    elif rr == 'r3':
                        bm |= 4
                self.current.clobbers = bm
                continue

            # instruction (inside function), with optional label operand
            if head == 'instr':
                if self.current is None:
                    raise ValueError(f"[line {lineno}] instr outside of a .libfn block")
                if len(toks) < 2:
                    raise ValueError(f"[line {lineno}] instr requires a mnemonic")
                mnem = toks[1]
                operand = 0
                if len(toks) >= 3:
                    op_tok = toks[2].strip()
                    # allow @label or label
                    label_name = None
                    if not op_tok.lower().startswith("0x"):
                        # try int; if fails, treat as label
                        try:
                            operand = self._parse_int(op_tok)
                        except ValueError:
                            label_name = op_tok[1:] if op_tok.startswith("@") else op_tok
                    if label_name:
                        # encode with operand=0 for now; remember to fix in build()
                        idx = len(self.current.body)
                        self._emit_instr(mnem, 0)
                        self.pending_instr_labels.append((self.current, idx, mnem, label_name))
                        continue
                # plain numeric operand or no operand
                self._emit_instr(mnem, operand)
                continue

            # Globals (outside function blocks)
            if head in ('data', 'q47', 'bits'):
                if self.current is not None:
                    raise ValueError(f"[line {lineno}] {head} not allowed inside .libfn (function bodies are instruction-only)")
                if self.glob_loc is None:
                    raise ValueError(f"[line {lineno}] {head} requires prior .constbase/.org for global placement")
                addr = self.glob_loc
                val_tok = toks[1] if len(toks) > 1 else None

                if head == 'data':
                    v = 0 if val_tok is None else self._parse_int(val_tok)
                    bits = to_twos_complement(v) & WORD_MASK
                elif head == 'q47':
                    v = 0.0 if val_tok is None else float(val_tok)
                    bits = to_twos_complement(float_to_q47(v)) & WORD_MASK
                else:  # bits
                    v = 0 if val_tok is None else self._parse_hex48(val_tok)
                    bits = v & WORD_MASK

                self.globals.append((addr, bits))
                self.glob_loc = addr + 1
                continue

            raise ValueError(f"[line {lineno}] Unknown library line: {line}")

    # ---------- build ----------
    def build(self, out_path: str):
        """
        Layout:
          [0] MAGIC, [1] VERSION, [2] ENTRY_COUNT, [3] TOC_START
          TOC entries (4 words each): [ID, NAMEHASH, START, LENGTH]
          Functions:
            [start+0] FNHDR_MAGIC (0x464E4844)
            [start+1] FN_META => (ABI_VER<<36)|(ARGS<<24)|(RETURNS<<16)|(CLOBBERS)
            [start+2] RESERVED (0)
            [start+3..] BODY (encoded instructions)
          Globals:
            Raw 48-bit words written by random access to specified absolute addresses.
        """
        # Compute function addresses
        toc_start       = 4
        fn_region_start = toc_start + len(self.functions) * 4
        ip              = fn_region_start
        for fn in self.functions:
            fn.start  = ip
            fn.length = 3 + len(fn.body)
            ip       += fn.length
        fn_region_end = ip  # first free word after all functions

        # Resolve pending label operands in function bodies
        for (fn_obj, body_idx, mnem, label_name) in self.pending_instr_labels:
            # Try local label first
            if label_name in fn_obj.local_labels:
                # Calculate absolute address: fn.start + 3 (header) + local_offset
                # Wait, JUMP target is absolute address.
                # fn.start is the address of FNHDR_MAGIC.
                # The body starts at fn.start + 3.
                # So target = fn.start + 3 + local_offset.
                local_offset = fn_obj.local_labels[label_name]
                target_addr = fn_obj.start + 3 + local_offset
                fn_obj.body[body_idx] = encode_instr(mnem, target_addr) & WORD_MASK
            elif label_name in self.global_labels:
                # Global label
                addr = self.global_labels[label_name]
                fn_obj.body[body_idx] = encode_instr(mnem, addr) & WORD_MASK
            else:
                raise ValueError(f"Unknown label referenced by '{mnem}': {label_name} (in function '{fn_obj.name}')")

        # Warn/error on global overlaps
        for (addr, _bits) in self.globals:
            if addr < fn_region_start:
                # overlaps header or TOC
                msg = (f"[lib_builder] WARNING: global address {addr} < fn_region_start {fn_region_start} "
                       f"(header/TOC overlap risk)")
                print(msg, file=sys.stderr)
            if fn_region_start <= addr < fn_region_end:
                # overlaps function area
                raise ValueError(
                    f"Global address {addr} overlaps function region "
                    f"[{fn_region_start}..{fn_region_end-1}]. Choose a higher .constbase/.org."
                )

        # Create/overwrite file
        with open(out_path, 'wb') as f:
            # Header
            for word in (LIB_MAGIC, VERSION, len(self.functions), toc_start):
                f.write(word_to_bytes(word & WORD_MASK))

            # TOC
            for fn in self.functions:
                for word in (fn.fn_id, fn.namehash, fn.start, fn.length):
                    f.write(word_to_bytes(word & WORD_MASK))

            # Functions (sequential write)
            for fn in self.functions:
                meta = ((ABI_VER & 0xFFF) << 36) | ((fn.args & 0xFF) << 24) | ((fn.returns & 0xFF) << 16) | (fn.clobbers & 0xFFFF)
                for word in (FNHDR_MAGIC, meta, 0):
                    f.write(word_to_bytes(word & WORD_MASK))
                for bits in fn.body:
                    f.write(word_to_bytes(bits & WORD_MASK))

        # Write globals by random access
        with open(out_path, 'r+b') as f:
            for (addr, bits) in self.globals:
                f.seek(addr * BYTE_PER_WORD)
                f.write(word_to_bytes(bits & WORD_MASK))

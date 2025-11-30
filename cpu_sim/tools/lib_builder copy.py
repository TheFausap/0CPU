
# lib_builder.py: build library.tape from library assembly (inline comment safe)
from typing import List
import os

from ..core.encoding import (
    WORD_MASK,
    to_twos_complement,
    float_to_q47,
    fnv1a_hash_48,
    word_to_bytes,
)
from ..core.opcodes import encode_instr

LIB_MAGIC = 0x4C49424844  # 'LIBHD' truncated to 48-bit
VERSION   = 0x000000000001


class LibFunction:
    def __init__(self, name: str, fn_id: int):
        self.name      = name
        self.fn_id     = fn_id
        self.namehash  = fnv1a_hash_48(name)
        self.args      = 0
        self.returns   = 0  # 0=r1, 1=r1:r2
        self.clobbers  = 0  # bit0=r1, bit1=r2, bit2=r3
        self.body: List[int] = []  # encoded instruction bits
        self.start     = 0
        self.length    = 0


class LibraryBuilder:
    """
    Library assembler → library.tape

    Directives:
      .libhdr
      .libfn <name> <id>
      .args <n>
      .returns r1 | r1:r2
      .clobbers r1[,r2[,r3]]
      instr <MNEMONIC> [OPERAND]
      .endl

    Notes:
      - Inline comments are supported with ';' or '#'.
      - Function TOC entry: [ID, NAMEHASH, START, LENGTH]
      - Function body header: [FNHDR_MAGIC, FN_META, RESERVED]
    """
    def __init__(self, text: str):
        self.text = text
        self.functions: List[LibFunction] = []
        self.current: LibFunction | None = None

    # ---------- helpers ----------
    @staticmethod
    def _strip_inline_comments(line: str) -> str:
        """Remove anything after ';' or '#' (inline comments)."""
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

    def _emit_instr(self, mnem: str, operand: int = 0):
        if self.current is None:
            raise ValueError("instr outside of a .libfn block")
        self.current.body.append(encode_instr(mnem, operand))

    # ---------- parse ----------
    def parse(self):
        for raw in self.text.splitlines():
            line = self._strip_inline_comments(raw).strip()
            if not line:
                continue

            toks = line.split()
            head = toks[0]

            # header directive
            if head == '.libhdr':
                continue

            # begin function
            if head == '.libfn':
                if len(toks) < 3:
                    raise ValueError(".libfn requires <name> <id>")
                name  = toks[1]
                fn_id = self._parse_int(toks[2])
                self.current = LibFunction(name, fn_id)
                self.functions.append(self.current)
                continue

            # end function
            if head == '.endl':
                self.current = None
                continue

            # metadata
            if head == '.args':
                if self.current is None:
                    raise ValueError(".args outside of a .libfn block")
                if len(toks) < 2:
                    raise ValueError(".args requires a number")
                self.current.args = self._parse_int(toks[1])
                continue

            if head == '.returns':
                if self.current is None:
                    raise ValueError(".returns outside of a .libfn block")
                if len(toks) < 2:
                    raise ValueError(".returns requires r1 or r1:r2")
                mode = toks[1].lower()
                self.current.returns = 1 if mode == 'r1:r2' else 0
                continue

            if head == '.clobbers':
                if self.current is None:
                    raise ValueError(".clobbers outside of a .libfn block")
                if len(toks) < 2:
                    raise ValueError(".clobbers requires a comma-separated list")
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

            # instruction
            if head == 'instr':
                if len(toks) < 2:
                    raise ValueError("instr requires a mnemonic")
                mnem = toks[1]
                operand = 0
                if len(toks) >= 3:
                    # operand can be decimal or 0x.. hex
                    operand = self._parse_int(toks[2])
                self._emit_instr(mnem, operand)
                continue

            # Unknown line
            raise ValueError(f"Unknown library line: {line}")

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
        """
        FNHDR_MAGIC = 0x464E4844

        # Compute addresses
        toc_start       = 4
        fn_region_start = toc_start + len(self.functions) * 4
        ip              = fn_region_start
        for fn in self.functions:
            fn.start  = ip
            fn.length = 3 + len(fn.body)
            ip       += fn.length

        # Write file
        with open(out_path, 'wb') as f:
            # Header
            f.write(word_to_bytes(LIB_MAGIC & WORD_MASK))
            f.write(word_to_bytes(VERSION   & WORD_MASK))
            f.write(word_to_bytes(len(self.functions) & WORD_MASK))
            f.write(word_to_bytes(toc_start & WORD_MASK))

            # TOC
            for fn in self.functions:
                f.write(word_to_bytes(fn.fn_id    & WORD_MASK))
                f.write(word_to_bytes(fn.namehash & WORD_MASK))
                f.write(word_to_bytes(fn.start    & WORD_MASK))
                f.write(word_to_bytes(fn.length   & WORD_MASK))

            # Functions
            for fn in self.functions:
                meta = ((0x001 & 0xFFF) << 36) | ((fn.args & 0xFF) << 24) | ((fn.returns & 0xFF) << 16) | (fn.clobbers & 0xFFFF)
                f.write(word_to_bytes(FNHDR_MAGIC & WORD_MASK))
                f.write(word_to_bytes(meta        & WORD_MASK))
                f.write(word_to_bytes(0))
                for bits in fn.body:
                    f.write(word_to_bytes(bits & WORD_MASK))


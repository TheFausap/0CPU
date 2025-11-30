# assembler.py: MiniAssembler (two-pass) with CALL forms, PB support, and inline comment stripping
from typing import List, Tuple
from ..core.encoding import WORD_MASK, to_twos_complement, float_to_q47, fnv1a_hash_48
from ..core.opcodes import (
    encode_instr, decode_op, OP,
    pack_call_operand,
    CALL_FLAG_PB,
    CALL_MODE_SCRATCH_ABS, CALL_MODE_LIB_ABS, CALL_MODE_LIB_IDX, CALL_MODE_LIB_NAME,
)

OPERAND_MASK_36 = (1 << 36) - 1
VALUE_MASK_28   = (1 << 28) - 1

class AsmItem:
    def __init__(self, addr: int, bits48: int, kind: str):
        self.addr   = addr
        self.bits48 = bits48 & WORD_MASK
        self.kind   = kind  # 'instr' or 'data'

    def __repr__(self):
        return f"AsmItem(addr={self.addr}, kind={self.kind}, bits=0x{self.bits48:012X})"


class MiniAssembler:
    def __init__(self, text: str):
        self.text = text
        self.labels = {}
        self.items: List[AsmItem] = []
        self.loc = 0
        self.start_addr: int = 0
        self.pending_labels: List[Tuple[str, int]] = []

    # ---------- helpers ----------
    def _strip_inline_comments(self, line: str) -> str:
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

    def _parse_int(self, tok: str) -> int:
        tok = tok.strip()
        if tok.lower().startswith("0x"):
            return int(tok, 16)
        return int(tok)

    def _parse_hex48(self, tok: str) -> int:
        h = tok.strip().lower()
        if h.startswith("0x"):
            h = h[2:]
        if len(h) > 12:
            raise ValueError("hex48 too long; expected up to 12 hex digits")
        h = h.zfill(12)
        return int(h, 16) & WORD_MASK

    def _add_item(self, addr: int, bits48: int, kind: str):
        self.items.append(AsmItem(addr, bits48, kind))

    def _check_36(self, v: int, ctx: str, lineno: int):
        if not (-(1 << 35) <= v <= (1 << 35) - 1):
            raise ValueError(f"[line {lineno}] {ctx}: operand out of signed 36-bit range ({v})")

    def _check_28(self, v: int, ctx: str, lineno: int):
        if not (0 <= v <= VALUE_MASK_28):
            raise ValueError(f"[line {lineno}] {ctx}: operand out of 28-bit range ({v})")

    # ---------- pass 1 ----------
    def pass1(self):
        for lineno, raw in enumerate(self.text.splitlines(), start=1):
            line = self._strip_inline_comments(raw).strip()
            if not line:
                continue

            # label:
            if line.endswith(":") and (" " not in line):
                label = line[:-1].strip()
                if not label:
                    raise ValueError(f"[line {lineno}] Empty label name")
                if label in self.labels:
                    raise ValueError(f"[line {lineno}] Duplicate label: {label}")
                self.labels[label] = self.loc
                continue

            toks = line.split()

            # directives
            if toks[0] == ".org" and len(toks) >= 2:
                self.loc = self._parse_int(toks[1])
                continue

            if toks[0] == ".start" and len(toks) >= 2:
                target = toks[1]
                try:
                    self.start_addr = self._parse_int(target)
                except ValueError:
                    self.pending_labels.append((target, -1))
                continue

            # instructions
            if toks[0] == "instr":
                if len(toks) < 2:
                    raise ValueError(f"[line {lineno}] instr syntax: missing mnemonic")

                # ---- Extended CALL forms ----
                if toks[1] == "CALL":
                    mode  = CALL_MODE_SCRATCH_ABS
                    flags = 0
                    value = 0
                    extra_words: List[int] = []

                    if len(toks) >= 3:
                        form = toks[2]
                        if form == "SCRATCH":
                            if len(toks) < 4:
                                raise ValueError(f"[line {lineno}] CALL SCRATCH requires an absolute address")
                            mode  = CALL_MODE_SCRATCH_ABS
                            value = self._parse_int(toks[3])
                            self._check_28(value, "CALL SCRATCH", lineno)

                        elif form == "LIBADDR":
                            if len(toks) < 4:
                                raise ValueError(f"[line {lineno}] CALL LIBADDR requires an absolute address")
                            mode  = CALL_MODE_LIB_ABS
                            value = self._parse_int(toks[3])
                            self._check_28(value, "CALL LIBADDR", lineno)

                        elif form == "LIBIDX":
                            if len(toks) < 4:
                                raise ValueError(f"[line {lineno}] CALL LIBIDX requires an index")
                            mode  = CALL_MODE_LIB_IDX
                            value = self._parse_int(toks[3])
                            self._check_28(value, "CALL LIBIDX", lineno)

                        elif form == "LIBNAME":
                            if len(toks) < 4:
                                raise ValueError(f"[line {lineno}] CALL LIBNAME requires a function name")
                            mode  = CALL_MODE_LIB_NAME
                            nhash = fnv1a_hash_48(toks[3])
                            extra_words.append(nhash & WORD_MASK)

                        else:
                            try:
                                mode  = CALL_MODE_SCRATCH_ABS
                                value = self._parse_int(toks[2])
                                self._check_28(value, "CALL SCRATCH fallback", lineno)
                            except ValueError:
                                raise ValueError(f"[line {lineno}] Unknown CALL form or invalid operand: '{form}'")

                    # PB optional
                    if "PB" in toks:
                        flags |= CALL_FLAG_PB
                        idxpb = toks.index("PB")
                        if idxpb + 1 >= len(toks):
                            raise ValueError(f"[line {lineno}] PB requires an address token")
                        addr_tok = toks[idxpb + 1].strip()
                        try:
                            pb_addr = self._parse_int(addr_tok[1:]) if addr_tok.startswith("@") else self._parse_int(addr_tok)
                            self._check_28(pb_addr, "PB address", lineno)
                            extra_words.append(pb_addr & WORD_MASK)
                        except Exception as e:
                            raise ValueError(f"[line {lineno}] PB syntax error: {e}")

                    bits = encode_instr("CALL", pack_call_operand(mode, flags, value))
                    self._add_item(self.loc, bits, "instr")
                    self.loc += 1

                    for ew in extra_words:
                        self._add_item(self.loc, ew, "data")
                        self.loc += 1

                    continue

                # ---- Non-CALL instruction path ----
                mnem = toks[1]
                operand = 0
                if len(toks) >= 3:
                    op_tok = toks[2]
                    try:
                        operand = self._parse_int(op_tok)
                        self._check_36(operand, f"instr {mnem}", lineno)
                    except ValueError:
                        operand = None
                        self.pending_labels.append((op_tok, self.loc))

                bits = encode_instr(mnem, 0 if operand is None else operand)
                self._add_item(self.loc, bits, "instr")
                self.loc += 1
                continue

            # data / q47 / bits
            if toks[0] in ("data", "q47", "bits"):
                if len(toks) >= 2 and toks[1].startswith("@"):
                    addr = self._parse_int(toks[1][1:])
                    val_tok = toks[2] if len(toks) > 2 else None
                else:
                    addr = self.loc
                    val_tok = toks[1] if len(toks) > 1 else None
                    self.loc += 1

                if toks[0] == "data":
                    v = 0 if val_tok is None else self._parse_int(val_tok)
                    self._check_36(v, "data directive", lineno)
                    bits = to_twos_complement(v)
                elif toks[0] == "q47":
                    v = 0.0 if val_tok is None else float(val_tok)
                    bits = to_twos_complement(float_to_q47(v))
                else:
                    v = 0 if val_tok is None else self._parse_hex48(val_tok)
                    bits = v

                self._add_item(addr, bits, "data")
                continue

            if toks[0].upper() == "HALT":
                bits = encode_instr("HALT", 0)
                self._add_item(self.loc, bits, "instr")
                self.loc += 1
                continue

            raise ValueError(f"[line {lineno}] Unknown line: {line}")

    # ---------- pass 2 ----------
    def pass2(self):
        for (lab, marker) in list(self.pending_labels):
            if marker == -1:
                if lab not in self.labels:
                    raise ValueError(f"Unknown start label: {lab}")
                self.start_addr = self.labels[lab]

        for (lab, marker) in list(self.pending_labels):
            if marker == -1:
                continue
            if lab not in self.labels:
                raise ValueError(f"Unknown label operand: {lab}")
            target = self.labels[lab]

            for i in range(len(self.items)):
                if self.items[i].addr == marker and self.items[i].kind == "instr":
                    op, _ = decode_op(self.items[i].bits48)
                    op_name = next((k for k, v in OP.items() if v == op), None)
                    if op_name is None:
                        raise ValueError("Internal: opcode name not found during label resolution")
                    self.items[i].bits48 = encode_instr(op_name, target)
                    break

    def assemble(self) -> Tuple[List[AsmItem], int]:
        self.pass1()
        self.pass2()
        return self.items, self.start_addr

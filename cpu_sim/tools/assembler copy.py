
# assembler.py: MiniAssembler (two-pass) with CALL forms, PB support, and inline comment stripping
from typing import List, Tuple
from ..core.encoding import WORD_MASK, to_twos_complement, float_to_q47, fnv1a_hash_48
from ..core.opcodes import (
    encode_instr, decode_op, OP,
    pack_call_operand,
    CALL_FLAG_PB,
    CALL_MODE_SCRATCH_ABS, CALL_MODE_LIB_ABS, CALL_MODE_LIB_IDX, CALL_MODE_LIB_NAME,
)

class AsmItem:
    """
    A single assembled item (either an instruction word or raw data word)
    that should be stored at `addr` on the scratchpad tape.
    """
    def __init__(self, addr: int, bits48: int, kind: str):
        self.addr   = addr
        self.bits48 = bits48 & WORD_MASK
        self.kind   = kind  # 'instr' or 'data'

    def __repr__(self):
        return f"AsmItem(addr={self.addr}, kind={self.kind}, bits=0x{self.bits48:012X})"


class MiniAssembler:
    """
    Simple two-pass assembler for our 48-bit ISA.

    Syntax (line based):
      ; or #           : comment (inline comments supported)
      label:           : define a label at current location counter

      .org <addr>      : set current location counter
      .start <addr|label>
                       : set start address used by cards builder (TXR target)

      instr <MNEMONIC> [OPERAND]
                       : emit an encoded instruction (48-bit: 12-bit opcode + 36-bit operand)

         CALL forms (extended):
           instr CALL SCRATCH <abs>
           instr CALL LIBADDR  <abs>
           instr CALL LIBIDX   <index>
           instr CALL LIBNAME  <name>
           (optional) PB @<addr>   or   PB <addr>

         Examples:
           instr CALL LIBIDX 0x01
           instr CALL LIBNAME FixMulRound PB @200
           instr CALL 1234               ; fallback: SCRATCH absolute 1234

      data [<int>]     : emit signed 48-bit two's complement word
                         (optional direct address: data @<addr> <int>)

      q47  <float>     : emit Q47 fixed-point word
                         (optional direct address: q47 @<addr> <float>)

      bits <hex48>     : emit raw 48-bit word (12 hex digits)
                         (optional direct address: bits @<addr> <hex48>)
    """
    def __init__(self, text: str):
        self.text = text
        self.labels = {}                      # label -> address
        self.items: List[AsmItem] = []        # assembled output items
        self.loc = 0                          # location counter
        self.start_addr: int = 0              # default start address
        self.pending_labels: List[Tuple[str, int]] = []  # (label_name, addr_of_instr_waiting_for_operand)

    # ---------- parsing helpers ----------
    def _strip_inline_comments(self, line: str) -> str:
        """Remove anything after ';' or '#' to support inline comments."""
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

    # ---------- pass 1: parse & emit with placeholders ----------
    def pass1(self):
        for raw in self.text.splitlines():
            # Remove trailing inline comments and trim
            line = self._strip_inline_comments(raw).strip()
            if not line:
                continue

            # label:
            if line.endswith(":") and (" " not in line):
                label = line[:-1].strip()
                if not label:
                    raise ValueError("Empty label name")
                self.labels[label] = self.loc
                continue

            toks = line.split()

            # directives
            if toks[0] == ".org" and len(toks) >= 2:
                self.loc = self._parse_int(toks[1])
                continue

            if toks[0] == ".start" and len(toks) >= 2:
                # .start may be a number or a label
                target = toks[1]
                try:
                    self.start_addr = self._parse_int(target)
                except ValueError:
                    # Will resolve in pass2
                    self.pending_labels.append((target, -1))
                continue

            # instructions
            if toks[0] == "instr":
                if len(toks) < 2:
                    raise ValueError("instr syntax: missing mnemonic")

                # ---- Extended CALL forms ----
                if toks[1] == "CALL":
                    mode  = CALL_MODE_SCRATCH_ABS
                    flags = 0
                    value = 0
                    extra_words: List[int] = []

                    # Parse the CALL form and possible value/name
                    # Supported forms:
                    #   CALL SCRATCH <abs>
                    #   CALL LIBADDR  <abs>
                    #   CALL LIBIDX   <index>
                    #   CALL LIBNAME  <name>
                    # Fallback: CALL <abs>
                    if len(toks) >= 3:
                        form = toks[2]
                        if form == "SCRATCH":
                            if len(toks) < 4:
                                raise ValueError("CALL SCRATCH requires an absolute address")
                            mode  = CALL_MODE_SCRATCH_ABS
                            value = self._parse_int(toks[3])

                        elif form == "LIBADDR":
                            if len(toks) < 4:
                                raise ValueError("CALL LIBADDR requires an absolute address")
                            mode  = CALL_MODE_LIB_ABS
                            value = self._parse_int(toks[3])

                        elif form == "LIBIDX":
                            if len(toks) < 4:
                                raise ValueError("CALL LIBIDX requires an index")
                            mode  = CALL_MODE_LIB_IDX
                            value = self._parse_int(toks[3])

                        elif form == "LIBNAME":
                            if len(toks) < 4:
                                raise ValueError("CALL LIBNAME requires a function name")
                            mode  = CALL_MODE_LIB_NAME
                            nhash = fnv1a_hash_48(toks[3])
                            extra_words.append(nhash & WORD_MASK)

                        else:
                            # Fallback: treat toks[2] as an immediate scratchpad address
                            # e.g., "instr CALL 1234"
                            try:
                                mode  = CALL_MODE_SCRATCH_ABS
                                value = self._parse_int(toks[2])
                            except ValueError:
                                raise ValueError(f"Unknown CALL form or invalid operand: '{form}'")

                    # PB optional: look for "PB @<addr>" or "PB <addr>"
                    if "PB" in toks:
                        flags |= CALL_FLAG_PB
                        try:
                            idxpb = toks.index("PB")
                            if idxpb + 1 >= len(toks):
                                raise ValueError("PB requires an address token")
                            addr_tok = toks[idxpb + 1]
                            pb_addr = self._parse_int(addr_tok[1:]) if addr_tok.startswith("@") else self._parse_int(addr_tok)
                            extra_words.append(pb_addr & WORD_MASK)
                        except Exception as e:
                            raise ValueError(f"PB syntax: PB @<addr> or PB <addr> ({e})")

                    # Emit CALL instruction
                    bits = encode_instr("CALL", pack_call_operand(mode, flags, value))
                    self._add_item(self.loc, bits, "instr")
                    self.loc += 1

                    # Emit extra immediates directly after CALL (NAMEHASH first, then PB address)
                    for ew in extra_words:
                        self._add_item(self.loc, ew, "data")
                        self.loc += 1

                    continue  # done with CALL

                # ---- Non-CALL instruction path ----
                mnem = toks[1]
                operand = 0
                if len(toks) >= 3:
                    op_tok = toks[2]
                    # operand may be label or number
                    try:
                        operand = self._parse_int(op_tok)
                    except ValueError:
                        # Defer label resolution to pass2
                        operand = None
                        self.pending_labels.append((op_tok, self.loc))

                bits = encode_instr(mnem, 0 if operand is None else operand)
                self._add_item(self.loc, bits, "instr")
                self.loc += 1
                continue

            # data / q47 / bits with optional direct address
            if toks[0] in ("data", "q47", "bits"):
                # forms:
                #   data <int>
                #   data @<addr> <int>
                #   q47  <float>
                #   q47  @<addr> <float>
                #   bits <hex48>
                #   bits @<addr> <hex48>
                if len(toks) >= 2 and toks[1].startswith("@"):
                    addr = self._parse_int(toks[1][1:])
                    val_tok = toks[2] if len(toks) > 2 else None
                else:
                    addr = self.loc
                    val_tok = toks[1] if len(toks) > 1 else None
                    self.loc += 1

                if toks[0] == "data":
                    v = 0 if val_tok is None else self._parse_int(val_tok)
                    bits = to_twos_complement(v)
                elif toks[0] == "q47":
                    v = 0.0 if val_tok is None else float(val_tok)
                    bits = to_twos_complement(float_to_q47(v))
                else:  # bits
                    v = 0 if val_tok is None else self._parse_hex48(val_tok)
                    bits = v

                self._add_item(addr, bits, "data")
                continue

            # convenience: bare HALT
            if toks[0].upper() == "HALT":
                bits = encode_instr("HALT", 0)
                self._add_item(self.loc, bits, "instr")
                self.loc += 1
                continue

            # unknown line
            raise ValueError(f"Unknown line: {line}")

    # ---------- pass 2: resolve pending label operands and .start ----------
    def pass2(self):
        # Resolve .start if it referenced a label
        for (lab, marker) in list(self.pending_labels):
            if marker == -1:
                if lab not in self.labels:
                    raise ValueError(f"Unknown start label: {lab}")
                self.start_addr = self.labels[lab]

        # Resolve instruction operands that were labels
        for (lab, marker) in list(self.pending_labels):
            if marker == -1:
                # already handled above
                continue
            if lab not in self.labels:
                raise ValueError(f"Unknown label operand: {lab}")
            target = self.labels[lab]

            # find the item at 'marker' and re-encode with resolved operand
            for i in range(len(self.items)):
                if self.items[i].addr == marker and self.items[i].kind == "instr":
                    # decode to find original opcode (for safety)
                    op, _ = decode_op(self.items[i].bits48)
                    op_name = next((k for k, v in OP.items() if v == op), None)
                    if op_name is None:
                        raise ValueError("Internal: opcode name not found during label resolution")

                    self.items[i].bits48 = encode_instr(op_name, target)
                    break  # resolved this one

    # ---------- public entry point ----------
    def assemble(self) -> Tuple[List[AsmItem], int]:
        self.pass1()
        self.pass2()
        return self.items, self.start_addr


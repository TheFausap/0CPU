
# cpu.py: complete CPU with boot, ALU, multi-device CALL/RET, TXR, I/O realism ops
from typing import List, Optional, Tuple

from .tape import CardReader, PaperTape
from .encoding import (
    to_twos_complement,
    from_twos_complement,
    from_tc36,
    WORD_MASK,
    FRAC_BITS,
)
from .observe import TraceSink
import time
from .opcodes import (
    decode_op,
    OP,
    CALL_FLAG_PB,
    CALL_MODE_SCRATCH_ABS,
    CALL_MODE_LIB_ABS,
    CALL_MODE_LIB_IDX,
    CALL_MODE_LIB_NAME,
)

LIB_MAGIC = 0x4C4942484400  # 'LIBHD' truncated to 48-bit


class CPU:
    """
    48-bit word CPU (Q47 fixed-point) with three registers r1, r2, r3.
    Executes tape-resident programs on scratchpad and library tapes.
    Supports:
      - Booting from cards (odd -> r1, even -> execute)
      - TXR: transfer/execute block from scratchpad
      - CALL/RET: multi-device (scratchpad/library) with LIBNAME + PB
      - ALU ops (ADD, NEG, MUL, DIV, ROUND, logic, pair-shifts)
      - I/O ops (READ_CARD, WRITE_TAPE)
      - I/O-realism ops (REWIND, FF, STATUS)
    """

    def __init__(
        self,
        scratchpad,
        library,
        card_reader: CardReader,
        paper_tape: PaperTape,
        verbose: bool = False,
    ):
        self.verbose = verbose
        # Registers (signed Q47 integers)
        self.r1: int = 0
        self.r2: int = 0
        self.r3: int = 0

        # Devices: TapeFile or TapeDevice (must implement read_bits/write_bits/read_word/write_word)
        self.scratchpad = scratchpad
        self.library = library
        self.card_reader = card_reader
        self.paper_tape = paper_tape

        # Multi-device CALL stack: entries are (device_obj, return_ip)
        self._ctx_stack: List[Tuple] = []
        self._current_dev = None

        # Reserved scratchpad shadow window for PB extras (beyond r1..r3)
        self.PB_SHADOW_BASE = 0x100000
        
        # Observability
        self.trace_sink = None          # type: Optional[TraceSink]
        self.metrics = {
            "instr_count": 0,
            "by_opcode": {},            # op_name -> count
            "by_device": {},            # device_tag -> count
            "errors": 0,
            "ctx_switches": 0,
            "max_stack_depth": 0,
        }
        self._anomaly_rules = []        # list of callables(event)->list[str]


    # -----------------------------------------------------------------------
    # Library helpers
    # -----------------------------------------------------------------------
    def _lib_header(self) -> Tuple[int, int, int]:
        """Return (version, entry_count, toc_start) from library header words."""
        magic = self.library.read_bits(0)
        if magic != LIB_MAGIC:
            raise ValueError("Invalid library magic header")
        version = self.library.read_bits(1)
        entry_count = self.library.read_bits(2)
        toc_start = self.library.read_bits(3)
        return version, entry_count, toc_start

    def _lib_toc_entry(self, toc_start: int, idx: int) -> Tuple[int, int, int, int]:
        """
        Read a 4-word TOC entry:
          [0] fn_id
          [1] namehash
          [2] start (absolute index of function header start)
          [3] length (words in function including header)
        """
        base = toc_start + idx * 4
        fn_id = self.library.read_bits(base + 0)
        namehash = self.library.read_bits(base + 1)
        start = self.library.read_bits(base + 2)
        length = self.library.read_bits(base + 3)
        return fn_id, namehash, start, length

    def _lib_resolve_idx(self, val: int) -> int:
        """
        Resolve a library call by either zero-based TOC index (preferred),
        or, if 'val' isn't a valid index, by matching function ID in TOC.
        Returns the first instruction word address (skip 3-word header).
        """
        _, entry_count, toc_start = self._lib_header()

        # First: treat 'val' as zero-based TOC index
        if 0 <= val < entry_count:
            _, _, start, _ = self._lib_toc_entry(toc_start, val)
            return start + 3

        # Fallback: treat 'val' as a function ID (48-bit stored in TOC)
        for i in range(entry_count):
            fn_id, _, start, _ = self._lib_toc_entry(toc_start, i)
            if fn_id == (val & WORD_MASK):
                return start + 3

        raise IndexError(f"Library index/ID not found: {val}")

    def _lib_resolve_name(self, namehash: int) -> int:
        """Resolve a 48-bit namehash to the function start (skip 3-word header)."""
        _, entry_count, toc_start = self._lib_header()
        if self.verbose:
            print(f"DEBUG: resolve_name {namehash} (0x{namehash:X}), count={entry_count}")
        for i in range(entry_count):
            _, nh, start, _ = self._lib_toc_entry(toc_start, i)
            if self.verbose:
                print(f"  Entry {i}: hash={nh} (0x{nh:X})")
            if nh == (namehash & WORD_MASK):
                return start + 3
        raise KeyError(f"Library function namehash not found: 0x{namehash:X}")

    # -----------------------------------------------------------------------
    # Execution loop on the active device
    # -----------------------------------------------------------------------
    def _execute_block(self, dev, start_ip: int):
        """
        Execute starting at (dev, start_ip) until HALT or RET (empty stack), or end-of-tape.
        This function SWITCHES the active device and keeps it updated when CALL/RET occur.
        """
        self._current_dev = dev
        ip = start_ip

        while True:
            # Stop if we reached end-of-tape (prevents infinite scanning of zero words)
            if hasattr(self._current_dev, "record_count"):
                if ip >= self._current_dev.record_count():
                    break

            bits48 = self._current_dev.read_bits(ip)
            if bits48 is None:
                # EOF (future-proof if devices return None on out-of-range)
                break

            next_ip = self.execute_encoded(self._current_dev, bits48, tape_ip=ip)

            if next_ip is None:
                # HALT or RET with empty stack
                break

            # Device may change inside execute_encoded (CALL/RET); always advance using next_ip
            ip = next_ip

    # -----------------------------------------------------------------------
    # ALU helpers
    # -----------------------------------------------------------------------
    @staticmethod
    def _clamp_q47(x: int) -> int:
        """Clamp to signed Q47 range [-2^47, 2^47 - 1].
           NOTE: With FRAC_BITS=45, this is actually Q3.45, but we keep the name for now or rename?
           The method uses hardcoded 47. We should use WORD_BITS-1.
           Wait, encoding.py defines WORD_BITS=48.
           The range of a 48-bit signed integer is always [-2^47, 2^47-1].
           The interpretation of bits changes (Q0.47 vs Q3.45), but the integer range of the container is the same.
           So _clamp_q47 is actually correct for ANY 48-bit signed integer container.
           However, let's verify if we need to change anything here.
           MIN = -(1 << 47) is correct for 48-bit two's complement.
           So this function is actually fine as "clamp to 48-bit signed integer".
           I will leave it but maybe add a comment.
        """
        MIN = -(1 << 47)
        MAX = (1 << 47) - 1
        return MIN if x < MIN else MAX if x > MAX else x

    @staticmethod
    def _mul_q47_pair(a: int, b: int) -> Tuple[int, int]:
        """
        Multiply two Q47 integers -> signed 96-bit Q94 across (high48, low48).
        Returns signed 48-bit components.
        """
        prod = a * b  # Q47 * Q47 -> Q94
        MIN96 = -(1 << 95)
        MAX96 = (1 << 95) - 1
        prod = MIN96 if prod < MIN96 else MAX96 if prod > MAX96 else prod

        if prod < 0:
            prod_bits = ((-prod) ^ ((1 << 96) - 1)) + 1
        else:
            prod_bits = prod
        prod_bits &= (1 << 96) - 1

        high_bits = (prod_bits >> 48) & WORD_MASK
        low_bits = prod_bits & WORD_MASK
        return from_twos_complement(high_bits), from_twos_complement(low_bits)

    @staticmethod
    def _round_q94_to_q47(high48: int, low48: int) -> int:
        """
        Round a Q94 value (stored in r1:r2) to a single Q47 (nearest, away from zero).
        """
        hb = to_twos_complement(high48)
        lb = to_twos_complement(low48)
        combined_bits = ((hb << 48) | lb) & ((1 << 96) - 1)

        # Signed decode 96-bit two's complement
        if combined_bits & (1 << 95):
            val_q94 = -(((~combined_bits) & ((1 << 96) - 1)) + 1)
        else:
            val_q94 = combined_bits

        half = 1 << (FRAC_BITS - 1)  # 0.5 in Q47
        val_q94 = val_q94 + half if val_q94 >= 0 else val_q94 - half

        # Shift down to Q47 and clamp
        MIN = -(1 << 47)
        MAX = (1 << 47) - 1
        out = val_q94 >> FRAC_BITS
        return MIN if out < MIN else MAX if out > MAX else out

    def _shift_pair_96(self, left: bool, count_signed_36bits: int):
        """Logical shift across r1:r2 treated as 96-bit quantity."""
        # Negative counts treated as 0, cap at 95
        count = max(0, min(95, int(count_signed_36bits)))
        hb = to_twos_complement(self.r1)
        lb = to_twos_complement(self.r2)
        combined = ((hb << 48) | lb) & ((1 << 96) - 1)
        if left:
            combined = (combined << count) & ((1 << 96) - 1)
        else:
            combined = combined >> count
        new_high = (combined >> 48) & WORD_MASK
        new_low = combined & WORD_MASK
        self.r1 = from_twos_complement(new_high)
        self.r2 = from_twos_complement(new_low)

    def _rotate_r1(self, left: bool, count_signed_36bits: int):
        """Circular shift on r1 (48-bit)."""
        count = int(count_signed_36bits) % 48
        if count == 0:
            return

        val = to_twos_complement(self.r1)  # 48-bit unsigned

        if left:
            # (val << count) | (val >> (48 - count))
            rotated = ((val << count) | (val >> (48 - count))) & WORD_MASK
        else:
            # (val >> count) | (val << (48 - count))
            rotated = ((val >> count) | (val << (48 - count))) & WORD_MASK

        self.r1 = from_twos_complement(rotated)
        
    # Observability helper
    def _device_tag(self, dev) -> str:
        if dev is self.scratchpad: return "scratchpad"
        if dev is self.library: return "library"
        if hasattr(self.card_reader, "tape") and dev is getattr(self.card_reader, "tape", None):
            return "cards"
        return getattr(dev, "__class__", type(dev)).__name__

    # Device hook
    def set_trace_sink(self, sink):
        self.trace_sink = sink

    def add_anomaly_rule(self, rule_callable):
        """rule(event_dict) -> list[str] of triggered rule IDs"""
        self._anomaly_rules.append(rule_callable)

    def _emit_trace(self, dev, ip, op_name, op_code, opr_bits, consumed_extra, pb_used, ctx_switch=False):
        if not self.trace_sink:
            return
        device = self._device_tag(dev)
        # decode signed operand when meaningful
        operand_dec = None
        if opr_bits is not None:
            try:
                from .encoding import from_tc36
                operand_dec = from_tc36(opr_bits)
            except Exception:
                operand_dec = None

        # get realism params if present
        latency_ms = getattr(dev, "ms_per_word", None)
        start_stop_ms = getattr(dev, "start_stop_ms", None)
        seq_only = getattr(dev, "sequential_only", None)
        pos = None
        if hasattr(dev, "get_position"):
            try:
                pos = dev.get_position()
            except Exception:
                pos = None

        event = {
            "ts": time.time(),
            "ip": ip,
            "device": device,
            "op_code": op_code,
            "op_name": op_name,
            "operand_raw": int(opr_bits) if (opr_bits is not None) else None,
            "operand_dec": operand_dec,
            "r1": int(self.r1),
            "r2": int(self.r2),
            "r3": int(self.r3),
            "stack_depth": len(self._ctx_stack),
            "ctx_switch": bool(ctx_switch),
            "extra_words": int(consumed_extra or 0),
            "pb_used": bool(pb_used),
            "dev_pos": pos,
            "error": getattr(dev, "last_error", False) or None,
            "latency_ms": latency_ms,
            "start_stop_ms": start_stop_ms,
            "seq_only": seq_only,
            "anomalies": [],
        }

        # run anomaly rules
        for rule in self._anomaly_rules:
            try:
                hits = rule(event) or []
                event["anomalies"].extend(hits)
            except Exception:
                pass

        # metrics update
        self.metrics["instr_count"] += 1
        self.metrics["max_stack_depth"] = max(self.metrics["max_stack_depth"], len(self._ctx_stack))
        self.metrics["by_device"][device] = 1 + self.metrics["by_device"].get(device, 0)
        self.metrics["by_opcode"][op_name] = 1 + self.metrics["by_opcode"].get(op_name, 0)
        if event["error"]:
            self.metrics["errors"] += 1
        if ctx_switch:
            self.metrics["ctx_switches"] += 1

        # emit
        self.trace_sink.emit(event)

    # -----------------------------------------------------------------------
    # Execute a single encoded instruction on 'dev'
    # -----------------------------------------------------------------------
    def execute_encoded(self, dev, bits48: int, tape_ip: Optional[int] = None) -> Optional[int]:
        op, opr_bits = decode_op(bits48)
        
        op_name = next((k for k, v in OP.items() if v == op), f"OP_{op:03X}")
        consumed_extra = 0
        pb_used = False
        ctx_switch = False

        def next_ip(ip, consumed_extra=0):
            return None if ip is None else ip + 1 + consumed_extra

        # ---- NOP (safe advance) ----
        if op == OP.get("NOP", 0x000):
            return next_ip(tape_ip)

        # ---- Memory & I/O ----
        if op == OP["STORE_R1"]:
            addr = from_tc36(opr_bits)
            if addr < 0:
                raise ValueError("Negative address for STORE_R1")
            dev.write_word(addr, self.r1)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["STORE_R3"]:
            addr = from_tc36(opr_bits)
            if addr < 0:
                raise ValueError("Negative address for STORE_R3")
            dev.write_word(addr, self.r3)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["LOAD_R1"]:
            addr = from_tc36(opr_bits)
            if addr < 0:
                raise ValueError("Negative address for LOAD_R1")
            self.r1 = dev.read_word(addr)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["LOAD_R2"]:
            addr = from_tc36(opr_bits)
            if addr < 0:
                raise ValueError("Negative address for LOAD_R2")
            self.r2 = dev.read_word(addr)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["LOAD_R3"]:
            addr = from_tc36(opr_bits)
            if addr < 0:
                raise ValueError("Negative address for LOAD_R3")
            self.r3 = dev.read_word(addr)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["CLEAR_R1"]:
            self.r1 = 0
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["CLEAR_R2"]:
            self.r2 = 0
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["CLEAR_R3"]:
            self.r3 = 0
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["WRITE_TAPE"]:
            self.paper_tape.write(self.r3)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["READ_CARD"]:
            v = self.card_reader.read_next()
            if v is not None:
                self.r3 = v
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        # ---- ALU ----
        elif op == OP["ADD"]:
            self.r1 = self._clamp_q47(self.r1 + self.r2)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["NEG"]:
            self.r1 = self._clamp_q47(-self.r1)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["MUL"]:
            # r2 * r3 -> r1:r2 (Q94 across the pair)
            hi, lo = self._mul_q47_pair(self.r2, self.r3)
            self.r1, self.r2 = hi, lo
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["DIV"]:
            # r1 / r2 -> r1 (scale numerator by Q47 before integer division)
            if self.r2 == 0:
                self.r1 = (1 << 47) - 1 if self.r1 >= 0 else -(1 << 47)
            else:
                self.r1 = self._clamp_q47((self.r1 << FRAC_BITS) // self.r2)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["ROUND"]:
            self.r1 = self._round_q94_to_q47(self.r1, self.r2)
            self.r2 = 0
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["AND"]:
            self.r1 = from_twos_complement(
                to_twos_complement(self.r1) & to_twos_complement(self.r2)
            )
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["OR"]:
            self.r1 = from_twos_complement(
                to_twos_complement(self.r1) | to_twos_complement(self.r2)
            )
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["XOR"]:
            self.r1 = from_twos_complement(
                to_twos_complement(self.r1) ^ to_twos_complement(self.r2)
            )
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["SHIFT_LEFT"]:
            self._shift_pair_96(left=True, count_signed_36bits=from_tc36(opr_bits))
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["SHIFT_RIGHT"]:
            self._shift_pair_96(left=False, count_signed_36bits=from_tc36(opr_bits))
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["ROTATE_LEFT"]:
            self._rotate_r1(left=True, count_signed_36bits=from_tc36(opr_bits))
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["ROTATE_RIGHT"]:
            self._rotate_r1(left=False, count_signed_36bits=from_tc36(opr_bits))
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["IMUL"]:
            # Integer multiplication: r1 = r2 * r3 (truncated to 48 bits)
            val = (self.r2 * self.r3) & WORD_MASK
            self.r1 = to_twos_complement(from_twos_complement(val)) # Ensure correct sign handling?
            # Wait, python ints are arbitrary precision.
            # r2, r3 are signed integers (from properties).
            # self.r2 * self.r3 gives correct signed result.
            # We just need to mask it to 48 bits and store.
            # But self.r1 setter expects signed int? No, cpu.py stores signed ints in r1/r2/r3?
            # Let's check properties.
            # self._r1 is stored as int. Properties r1/r2/r3 return signed int.
            # Setter takes int and clamps/masks?
            # cpu.py:
            # @property
            # def r1(self): return from_twos_complement(self._r1)
            # @r1.setter
            # def r1(self, val): self._r1 = to_twos_complement(val)
            
            # So if I assign `self.r1 = self.r2 * self.r3`, it will be converted to twos complement.
            # Correct.
            self.r1 = self.r2 * self.r3
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["IDIV"]:
            # Integer division: r1 = r2 / r3
            if self.r3 == 0:
                # Division by zero? Return 0 or max?
                # Let's return 0 for now or raise error?
                # Raising error is safer.
                raise ValueError("Division by zero (IDIV)")
            self.r1 = int(self.r2 / self.r3) # int() truncates towards zero?
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["SUB"]:
            # Integer subtraction: r1 = r2 - r3
            self.r1 = self.r2 - self.r3
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        # ---- Control flow (scratchpad) ----
        elif op == OP["SKIP"]:
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return None if tape_ip is None else tape_ip + 2

        elif op == OP["SKIP_IF_ZERO"]:
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return (None if tape_ip is None else tape_ip + 2) if self.r1 == 0 else next_ip(tape_ip)

        elif op == OP["SKIP_IF_NONZERO"]:
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return (None if tape_ip is None else tape_ip + 2) if self.r1 != 0 else next_ip(tape_ip)

        elif op == OP["TXR"]:
            # Transfer & execute block on scratchpad (operand is a signed 36-bit address)
            start = from_tc36(opr_bits)
            if start < 0:
                raise ValueError("Negative address for TXR")
            # TXR is a jump to scratchpad; caller handles execution loop
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return start

        elif op == OP["SLOAD_R1"]:
            # Always load from scratchpad, even when executing on library tape
            addr = from_tc36(opr_bits)
            if addr < 0:
                raise ValueError("Negative address for SLOAD_R1")
            self.r1 = self.scratchpad.read_word(addr)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["SLOAD_R2"]:
            addr = from_tc36(opr_bits)
            if addr < 0:
                raise ValueError("Negative address for SLOAD_R2")
            self.r2 = self.scratchpad.read_word(addr)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP["SLOAD_R3"]:
            addr = from_tc36(opr_bits)
            if addr < 0:
                raise ValueError("Negative address for SLOAD_R3")
            self.r3 = self.scratchpad.read_word(addr)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP.get("JUMP"):
            # Absolute jump
            target = from_tc36(opr_bits)
            if target < 0:
                raise ValueError("Negative address for JUMP")
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return target

        elif op == OP["HALT"]:
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return None

        # ---- CALL / RET (multi-device, LIBNAME & PB support) ----
        elif op == OP["CALL"]:
            mode = (opr_bits >> 32) & 0xF
            flags = (opr_bits >> 28) & 0xF
            value = opr_bits & ((1 << 28) - 1)
            consumed_extra = 0
            pb_used = False
            # Extra immediate for LIBNAME
            namehash = None
            if mode == CALL_MODE_LIB_NAME:
                namehash = dev.read_bits(tape_ip + 1)
                if namehash is None:
                    raise ValueError("CALL LIBNAME missing namehash immediate")
                consumed_extra += 1

            # Extra immediate for PB
            pb_addr = None
            if flags & CALL_FLAG_PB:
                pb_addr = dev.read_bits(tape_ip + 1 + consumed_extra)
                if pb_addr is None:
                    raise ValueError("CALL PB missing PB address immediate")
                consumed_extra += 1
                pb_used = True

            # PB mapping (PB[0]=count, PB[1..] args)
            if pb_addr is not None:
                count = self.scratchpad.read_word(pb_addr) or 0
                count = max(0, int(count))
                if count >= 1:
                    self.r1 = self.scratchpad.read_word(pb_addr + 1)
                if count >= 2:
                    self.r2 = self.scratchpad.read_word(pb_addr + 2)
                if count >= 3:
                    self.r3 = self.scratchpad.read_word(pb_addr + 3)
                # copy extras into shadow window
                extra = max(0, count - 3)
                for i in range(extra):
                    val = self.scratchpad.read_word(pb_addr + 4 + i)
                    self.scratchpad.write_word(self.PB_SHADOW_BASE + i, val)

            # Resolve target address/device
            if mode == CALL_MODE_SCRATCH_ABS:
                target_dev, target_ip = self.scratchpad, value
            elif mode == CALL_MODE_LIB_ABS:
                target_dev, target_ip = self.library, value
            elif mode == CALL_MODE_LIB_IDX:
                target_dev, target_ip = self.library, self._lib_resolve_idx(value)
            elif mode == CALL_MODE_LIB_NAME:
                target_dev, target_ip = self.library, self._lib_resolve_name(namehash)
            else:
                raise ValueError("Unknown CALL mode")

            # Push return context and jump to target: SWITCH device here
            self._ctx_stack.append((dev, tape_ip + 1 + consumed_extra))
            self._current_dev = target_dev
            ctx_switch = True
            # emit trace for CALL at current ip
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=True)
            return target_ip

        elif op == OP["RET"]:
            if self._ctx_stack:
                dev_ret, ip_ret = self._ctx_stack.pop()
                # SWITCH device back to caller
                self._current_dev = dev_ret
                ctx_switch = True
                self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=True)
                return ip_ret
            else:
                self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=False)
                return None

        # ---- I/O realism ops ----
        elif op == OP.get("REWIND"):
            # operand: device (0=scratchpad,1=library,2=cards)
            dev_code = from_tc36(opr_bits)
            if dev_code == 0 and hasattr(self.scratchpad, "rewind"):
                self.scratchpad.rewind()
            elif dev_code == 1 and hasattr(self.library, "rewind"):
                self.library.rewind()
            elif dev_code == 2 and hasattr(self.card_reader, "tape") and hasattr(self.card_reader.tape, "rewind"):
                self.card_reader.tape.rewind()
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP.get("FF"):
            # dev in top 12 bits, count in low 24 bits
            dev_code = (opr_bits >> 24) & 0xFFF
            count = opr_bits & ((1 << 24) - 1)
            target_ff = None
            if dev_code == 0 and hasattr(self.scratchpad, "fast_forward"):
                target_ff = self.scratchpad.fast_forward
            elif dev_code == 1 and hasattr(self.library, "fast_forward"):
                target_ff = self.library.fast_forward
            elif dev_code == 2 and hasattr(self.card_reader, "tape") and hasattr(self.card_reader.tape, "fast_forward"):
                target_ff = self.card_reader.tape.fast_forward
            if target_ff:
                target_ff(count)
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        elif op == OP.get("STATUS"):
            dev_code = from_tc36(opr_bits)
            pos = 0
            if dev_code == 0 and hasattr(self.scratchpad, "get_position"):
                pos = self.scratchpad.get_position()
            elif dev_code == 1 and hasattr(self.library, "get_position"):
                pos = self.library.get_position()
            elif dev_code == 2 and hasattr(self.card_reader, "tape") and hasattr(self.card_reader.tape, "get_position"):
                pos = self.card_reader.tape.get_position()
            self.r3 = pos
            self._emit_trace(dev, tape_ip, op_name, op, opr_bits, consumed_extra, pb_used, ctx_switch=ctx_switch)
            return next_ip(tape_ip)

        # ---- Unknown opcode -> safe advance ----
        return next_ip(tape_ip)

    # -----------------------------------------------------------------------
    # Boot from cards (odd -> r1, even -> execute on scratchpad)
    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # Boot from cards (odd -> r1, even -> execute on scratchpad)
    # -----------------------------------------------------------------------
    def boot_tick(self) -> Tuple[bool, Optional[int]]:
        """
        Perform one step of the boot process.
        Returns (done, start_ip).
        - done=True, start_ip=None: EOF (no more cards)
        - done=True, start_ip=int:  TXR encountered, transfer to this IP
        - done=False:               Continue booting
        """
        if not hasattr(self, '_boot_idx'):
            self._boot_idx = 1

        val = self.card_reader.read_next()
        if val is None:
            return True, None # EOF

        start_ip = None
        if self._boot_idx % 2 == 1:
            # odd card: load r1 directly (signed int from tape)
            self.r1 = val
        else:
            # even card: execute encoded instruction (use raw bits)
            bits = to_twos_complement(val)
            # execute_encoded returns next_ip (None if HALT, or target if JUMP/TXR)
            # For TXR, it now returns the target address.
            start_ip = self.execute_encoded(self.scratchpad, bits, tape_ip=None)

        self._boot_idx += 1
        
        if start_ip is not None:
            return True, start_ip
            
        return False, None

    def boot_from_cards(self):
        self._boot_idx = 1
        while True:
            done, start_ip = self.boot_tick()
            if done:
                if start_ip is not None:
                    # TXR encountered: execute block on scratchpad (blocking)
                    self._execute_block(self.scratchpad, start_ip)
                break

# cards_builder.py — safer bits-only emission and early guards
from ..core.opcodes import encode_instr
from ..core.encoding import (
    from_twos_complement,  # for decoding raw data bits -> signed
    WORD_BITS,
    WORD_MASK,
)
WORD_HEX_DIGITS = WORD_BITS // 4

class CardsBuilder:
    def __init__(self, cards_tape):
        """
        cards_tape: must implement append_bits() or write_bits(idx, bits) for instruction words,
                    and append_word() for signed data words.
        """
        self.cards = cards_tape
        self.listing = []

    def _fmt_hex(self, x: int) -> str:
        return f"0x{x:0{WORD_HEX_DIGITS}X}"

    def _append_bits_strict(self, bits48: int):
        """Emit raw 48-bit instruction; never use data path."""
        bits48 &= WORD_MASK
        if hasattr(self.cards, "append_bits"):
            self.cards.append_bits(bits48)
            return
        if hasattr(self.cards, "get_size_words") and hasattr(self.cards, "write_bits"):
            idx = self.cards.get_size_words()
            self.cards.write_bits(idx, bits48)
            return
        # No safe path to write raw bits: fail fast
        raise RuntimeError("Cards device lacks append_bits/write_bits; cannot emit raw instruction bits safely.")

    def append_pair_store(self, word_bits: int, store_addr: int):
        """
        Emit two cards:
        - Odd: DATA -> r1 (input is raw 48-bit two's-complement bits)
        - Even: EXEC STORE_R1 <store_addr> (raw encoded instruction)
        """
        # Guard: CPU rejects negative addresses for STORE_R1
        if store_addr < 0:
            raise ValueError(f"STORE_R1 expects non-negative address; got {store_addr}")

        # Odd card: data -> r1 (decode bits to signed int, then append_word)
        signed_val = from_twos_complement(word_bits & WORD_MASK)
        self.cards.append_word(signed_val)
        self.listing.append(f"DATA {self._fmt_hex(word_bits)} -> r1")

        # Even card: raw instruction bits (strict bits path)
        instr_bits = encode_instr("STORE_R1", store_addr) & WORD_MASK
        self._append_bits_strict(instr_bits)
        self.listing.append(f"EXEC STORE_R1 0x{store_addr:X}")

    def finalize_boot(self, start_addr: int):
        """
        Emit boot sequence:
        - DATA 0 -> r1
        - EXEC TXR <start_addr>
        """
        # Odd card: data 0 -> r1
        self.cards.append_word(0)
        self.listing.append(f"DATA {self._fmt_hex(0)} -> r1")

        # Even card: TXR <start_addr> (strict bits path)
        txr_bits = encode_instr("TXR", start_addr) & WORD_MASK
        self._append_bits_strict(txr_bits)
        self.listing.append(f"EXEC TXR 0x{start_addr:X}")

    def save_listing(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            for line in self.listing:
                f.write(line + "\n")

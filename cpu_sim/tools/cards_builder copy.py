# cards_builder.py: CardsBuilder class
from ..core.opcodes import encode_instr
from ..core.encoding import from_twos_complement

class CardsBuilder:
    def __init__(self, cards_tape):
        self.cards = cards_tape
        self.listing = []
    def append_pair_store(self, word_bits: int, store_addr: int):
        # odd card: data -> r1 (signed write of raw bits)
        self.cards.append_word(from_twos_complement(word_bits))
        self.listing.append(f"DATA    0x{word_bits:012X} -> r1")
        # even card: execute STORE_R1 addr
        instr_bits = encode_instr("STORE_R1", store_addr)
        self.cards.append_word(from_twos_complement(instr_bits))
        self.listing.append(f"EXEC    STORE_R1 {store_addr}")
    def finalize_boot(self, start_addr: int):
        self.cards.append_word(0)
        self.listing.append("DATA    0 -> r1")
        txr_bits = encode_instr("TXR", start_addr)
        self.cards.append_word(from_twos_complement(txr_bits))
        self.listing.append(f"EXEC    TXR {start_addr}")
    def save_listing(self, path: str):
        with open(path, "w") as f:
            for line in self.listing: f.write(line + "\n")

# tape.py: TapeFile, CardReader, PaperTape
import os
import time
from .encoding import BYTE_PER_WORD, WORD_MASK, to_twos_complement, from_twos_complement, word_to_bytes, bytes_to_word

class TapeFile:
    def __init__(self, path: str):
        self.path = path
        self.last_action = None
        self.last_error = False
        self._last_action_time = 0
        if not os.path.exists(self.path):
            with open(self.path, "wb"): pass

    def _mark_action(self, action):
        self.last_action = action
        self._last_action_time = time.time()
        
    def _ensure_size(self, n_records: int):
        size = os.path.getsize(self.path)
        current = size // BYTE_PER_WORD
        if current < n_records:
            with open(self.path, "ab") as f:
                f.write(b"\x00" * (BYTE_PER_WORD * (n_records - current)))
    def read_word(self, index: int) -> int:
        size = os.path.getsize(self.path)
        off = index * BYTE_PER_WORD
        if off + BYTE_PER_WORD > size: return 0
        with open(self.path, "rb") as f:
            f.seek(off)
            b = f.read(BYTE_PER_WORD)
            return from_twos_complement(bytes_to_word(b)) if len(b) == BYTE_PER_WORD else 0
    def write_word(self, index: int, value: int):
        self._ensure_size(index + 1)
        bits = to_twos_complement(value)
        with open(self.path, "r+b") as f:
            f.seek(index * BYTE_PER_WORD)
            f.write(word_to_bytes(bits))
    def append_word(self, value: int) -> int:
        size = os.path.getsize(self.path)
        idx = size // BYTE_PER_WORD
        self.write_word(idx, value)
        return idx
    def read_bits(self, index: int) -> int:
        self._mark_action('read')
        size = os.path.getsize(self.path)
        off = index * BYTE_PER_WORD
        if off + BYTE_PER_WORD > size: return 0
        with open(self.path, "rb") as f:
            f.seek(off)
            b = f.read(BYTE_PER_WORD)
            return bytes_to_word(b) if len(b) == BYTE_PER_WORD else 0
    def write_bits(self, index: int, bits48: int):
        self._mark_action('write')
        self._ensure_size(index + 1)
        with open(self.path, "r+b") as f:
            f.seek(index * BYTE_PER_WORD)
            f.write(word_to_bytes(bits48 & WORD_MASK))
    def append_bits(self, bits48: int) -> int:
        size = os.path.getsize(self.path)
        idx = size // BYTE_PER_WORD
        self.write_bits(idx, bits48)
        return idx
    def record_count(self) -> int:
        return os.path.getsize(self.path) // BYTE_PER_WORD

class CardReader:
    def __init__(self, tape: 'TapeFile'):
        self.tape = tape
        self.pos = 0
    def read_next(self):
        if self.pos >= self.tape.record_count(): return None
        val = self.tape.read_word(self.pos)
        self.pos += 1
        return val

class PaperTape:
    def __init__(self, tape: 'TapeFile'):
        self.tape = tape
    def write(self, value: int) -> int:
        return self.tape.append_word(value)

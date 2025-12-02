# tape.py: TapeFile, CardReader, PaperTape
import os
import time
from .encoding import (
    BYTE_PER_WORD,
    WORD_MASK,
    to_twos_complement,
    from_twos_complement,
    word_to_bytes,
    bytes_to_word,
)

class TapeFile:
    def __init__(self, path: str):
        self.path = path
        self.last_action = None
        self.last_error = False
        self._last_action_time = 0
        self._pos = 0  # track last touched record index for STATUS/FF/REWIND
        if not os.path.exists(self.path):
            with open(self.path, "wb"):
                pass

    def _mark_action(self, action, index=None):
        self.last_action = action
        self._last_action_time = time.time()
        if index is not None:
            # best-effort: clamp to valid range
            max_idx = max(0, self.record_count() - 1)
            self._pos = max(0, min(max_idx, index))

    def _ensure_size(self, n_records: int):
        size = os.path.getsize(self.path)
        current = size // BYTE_PER_WORD
        if current < n_records:
            with open(self.path, "ab") as f:
                f.write(b"\x00" * (BYTE_PER_WORD * (n_records - current)))

    # --- Signed word (program data) I/O ---
    def read_word(self, index: int) -> int:
        size = os.path.getsize(self.path)
        off = index * BYTE_PER_WORD
        if off + BYTE_PER_WORD > size:
            self._mark_action('read_word', index)
            return 0
        with open(self.path, "rb") as f:
            f.seek(off)
            b = f.read(BYTE_PER_WORD)
        self._mark_action('read_word', index)
        return from_twos_complement(bytes_to_word(b)) if len(b) == BYTE_PER_WORD else 0

    def write_word(self, index: int, value: int):
        self._ensure_size(index + 1)
        bits = to_twos_complement(value)
        with open(self.path, "r+b") as f:
            f.seek(index * BYTE_PER_WORD)
            f.write(word_to_bytes(bits))
        self._mark_action('write_word', index)

    def append_word(self, value: int) -> int:
        size = os.path.getsize(self.path)
        idx = size // BYTE_PER_WORD
        self.write_word(idx, value)
        self._mark_action('append_word', idx)
        return idx

    # --- Raw bits (instruction fetch/encode) I/O ---
    def read_bits(self, index: int):
        # Return None if out-of-range to signal EOF to CPU
        size = os.path.getsize(self.path)
        off = index * BYTE_PER_WORD
        if off + BYTE_PER_WORD > size:
            self._mark_action('read_bits', index)
            return None
        with open(self.path, "rb") as f:
            f.seek(off)
            b = f.read(BYTE_PER_WORD)
        self._mark_action('read_bits', index)
        val = bytes_to_word(b) if len(b) == BYTE_PER_WORD else None
        if "scratchpad" in self.path and index >= 200:
            print(f"DEBUG: TapeFile({self.path}).read_bits({index}) -> 0x{val:X}")
        return val

    def write_bits(self, index: int, bits48: int):
        self._ensure_size(index + 1)
        with open(self.path, "r+b") as f:
            f.seek(index * BYTE_PER_WORD)
            f.write(word_to_bytes(bits48 & WORD_MASK))
        self._mark_action('write_bits', index)

    def append_bits(self, bits48: int) -> int:
        size = os.path.getsize(self.path)
        idx = size // BYTE_PER_WORD
        self.write_bits(idx, bits48)
        self._mark_action('append_bits', idx)
        return idx

    def record_count(self) -> int:
        return os.path.getsize(self.path) // BYTE_PER_WORD

    # --- I/O realism hooks expected by CPU ---
    def rewind(self):
        # Move position to start; executor uses indices it manages, but STATUS reflects this
        self._mark_action('rewind', 0)

    def fast_forward(self, count: int):
        # Advance position without changing content; clamp to end
        try:
            c = int(count)
        except Exception:
            c = 0
        new_pos = min(self.record_count(), self._pos + max(0, c))
        # if new_pos == record_count(), status shows end-of-tape position
        self._mark_action('fast_forward', max(0, new_pos))

    def get_position(self) -> int:
        # Report last touched record index (best-effort)
        return self._pos


class CardReader:
    def __init__(self, tape: 'TapeFile'):
        self.tape = tape
        self.pos = 0

    def read_next(self):
        if self.pos >= self.tape.record_count():
            return None
        val = self.tape.read_word(self.pos)
        # Update tape's position for STATUS consistency
        self.tape._mark_action('card_read_next', self.pos)
        self.pos += 1
        return val


class PaperTape:
    def __init__(self, tape: 'TapeFile'):
        self.tape = tape

    def write(self, value: int) -> int:
        idx = self.tape.append_word(value)
        # Update tape's position for STATUS consistency
        self.tape._mark_action('paper_write', idx)
        return idx

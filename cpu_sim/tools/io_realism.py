# io_realism.py: TapeDevice abstraction
import os, time, random
from ..core.encoding import BYTE_PER_WORD, WORD_MASK, to_twos_complement, from_twos_complement, word_to_bytes, bytes_to_word

class TapeDevice:
    def __init__(self, path: str, sequential_only: bool = True, ms_per_word: int = 2, start_stop_ms: int = 50, error_rate: float = 0.0, max_retries: int = 3, on_wait=None, ips: float = 0.0, density: int = 200):
        self.path = path
        self.sequential_only = sequential_only
        
        # Calculate ms_per_word from IPS/Density if provided
        if ips > 0:
            # Density is characters (bytes) per inch.
            # Word is 6 bytes.
            words_per_inch = density / 6.0
            words_per_sec = words_per_inch * ips
            self.ms_per_word = 1000.0 / words_per_sec
        else:
            self.ms_per_word = ms_per_word
            
        self.start_stop_ms = start_stop_ms
        self.error_rate = error_rate
        self.max_retries = max_retries
        self.on_wait = on_wait
        self.ips = ips
        self.density = density
        self.position = 0
        self.last_action = None
        self.last_error = False
        self._last_action_time = 0
        if not os.path.exists(self.path):
            with open(self.path, 'wb'): pass
            
    def _mark_action(self, action):
        self.last_action = action
        self._last_action_time = time.time()

    def _simulate_latency(self, words: int = 1, action_name: str = "moving"):
        # Simulate motor spin-up only if idle for a while
        now = time.time()
        latency = self.ms_per_word * words
        if (now - self._last_action_time) > 0.2: # Motor spins down after 200ms idle
            latency += self.start_stop_ms
        
        if latency > 100 and self.on_wait: # Only report significant delays (>100ms)
            self.on_wait(f"{action_name} ({latency:.0f}ms)")
            
        time.sleep(latency / 1000.0)

    def _inject_error(self) -> bool:
        err = random.random() < self.error_rate
        if err:
            self.last_error = True
            self._last_action_time = time.time()
        return err

    def _ensure_size(self, n_records: int):
        size = os.path.getsize(self.path)
        current_records = size // BYTE_PER_WORD
        if current_records < n_records:
            with open(self.path, 'ab') as f:
                f.write(b'\x00' * (BYTE_PER_WORD * (n_records - current_records)))

    def rewind(self):
        self._simulate_latency(self.position, "rewinding")
        self.position = 0

    def fast_forward(self, n: int):
        n = max(0, int(n))
        self._simulate_latency(n, "seeking")
        self.position += n

    def seek(self, index: int):
        index = max(0, int(index))
        if index == self.position:
            return
        if self.sequential_only:
            if index < self.position:
                self.rewind()
            self.fast_forward(index - self.position)
        else:
            # random access allowed; simulate latency proportional to distance
            distance = abs(index - self.position)
            self._simulate_latency(distance, "seeking")
            self.position = index

    def read_next(self):
        self._simulate_latency(1)
        for attempt in range(self.max_retries):
            if self._inject_error():
                if attempt < self.max_retries - 1:
                    continue
                else:
                    raise IOError("Tape read error after retries")
            break
        size = os.path.getsize(self.path)
        off = self.position * BYTE_PER_WORD
        if off + BYTE_PER_WORD > size:
            return None
        with open(self.path, 'rb') as f:
            f.seek(off)
            b = f.read(BYTE_PER_WORD)
            if len(b) < BYTE_PER_WORD:
                return None
            self.position += 1
            return from_twos_complement(bytes_to_word(b))

    def write_next(self, value: int):
        self._simulate_latency(1)
        for attempt in range(self.max_retries):
            if self._inject_error():
                if attempt < self.max_retries - 1:
                    continue
                else:
                    raise IOError("Tape write error after retries")
            break
        self._ensure_size(self.position + 1)
        bits = to_twos_complement(value)
        with open(self.path, 'r+b') as f:
            f.seek(self.position * BYTE_PER_WORD)
            f.write(word_to_bytes(bits))
        self.position += 1

    # Random-access compatibility for CPU
    def read_bits(self, index: int) -> int:
        self._mark_action('read')
        self.seek(index)
        self._simulate_latency(1)
        size = os.path.getsize(self.path)
        off = index * BYTE_PER_WORD
        if off + BYTE_PER_WORD > size:
            return None                         
        with open(self.path, 'rb') as f:
            f.seek(off)
            b = f.read(BYTE_PER_WORD)
        if len(b) < BYTE_PER_WORD:
            return None
        self.position = index + 1
        return bytes_to_word(b)


    def write_bits(self, index: int, bits48: int):
        self._mark_action('write')
        self.seek(index)
        self._ensure_size(index + 1)
        self._simulate_latency(1)
        with open(self.path, 'r+b') as f:
            f.seek(index * BYTE_PER_WORD)
            f.write(word_to_bytes(bits48 & WORD_MASK))
        self.position = index + 1

    def read_word(self, index: int) -> int:
        return from_twos_complement(self.read_bits(index))

    def write_word(self, index: int, value: int):
        self.write_bits(index, to_twos_complement(value))

    def get_position(self) -> int:
        return self.position

    def get_size_words(self) -> int:
        return os.path.getsize(self.path) // BYTE_PER_WORD

    def status(self):
        return {
            'position': self.position,
            'size_words': self.get_size_words(),
            'sequential_only': self.sequential_only,
            'ms_per_word': self.ms_per_word,
            'error_rate': self.error_rate
        }

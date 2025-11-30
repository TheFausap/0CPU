# encoding.py: 48-bit helpers, Q47 fixed-point, instruction encoding
from typing import Tuple

WORD_BITS = 48
BYTE_PER_WORD = 6
WORD_MASK = (1 << WORD_BITS) - 1
SIGN_BIT = 1 << (WORD_BITS - 1)
FRAC_BITS = 47

MIN_WORD = -(1 << (WORD_BITS - 1))
MAX_WORD = (1 << (WORD_BITS - 1)) - 1

OPR_BITS = 36
OPR_MASK = (1 << OPR_BITS) - 1
OPR_SIGN = 1 << (OPR_BITS - 1)


def clamp_word(val: int) -> int:
    return MIN_WORD if val < MIN_WORD else MAX_WORD if val > MAX_WORD else val


def to_twos_complement(val: int) -> int:
    val = clamp_word(val)
    if val < 0:
        val = ((-val) ^ WORD_MASK) + 1
        val &= WORD_MASK
    return val & WORD_MASK


def from_twos_complement(bits: int) -> int:
    bits &= WORD_MASK
    if bits & SIGN_BIT:
        return -(((~bits) & WORD_MASK) + 1)
    else:
        return bits


def word_to_bytes(bits: int) -> bytes:
    return bits.to_bytes(BYTE_PER_WORD, byteorder="big", signed=False)


def bytes_to_word(b: bytes) -> int:
    return int.from_bytes(b, byteorder="big", signed=False) & WORD_MASK


def float_to_q47(x: float) -> int:
    if x >= 1.0:
        x = 1.0 - (1.0 / (1 << FRAC_BITS))
    if x < -1.0:
        x = -1.0
    return clamp_word(int(round(x * (1 << FRAC_BITS))))


def q47_to_float(val: int) -> float:
    return float(val) / float(1 << FRAC_BITS)


def clamp36(val: int) -> int:
    min36 = -(1 << (OPR_BITS - 1))
    max36 = (1 << (OPR_BITS - 1)) - 1
    return max(min36, min(max36, val))


def to_tc36(val: int) -> int:
    val = clamp36(val)
    if val < 0:
        val = ((-val) ^ OPR_MASK) + 1
        val &= OPR_MASK
    return val & OPR_MASK


def from_tc36(bits: int) -> int:
    bits &= OPR_MASK
    if bits & OPR_SIGN:
        return -(((~bits) & OPR_MASK) + 1)
    else:
        return bits


FNV_OFFSET64 = 0xcbf29ce484222325
FNV_PRIME64  = 0x100000001b3

def fnv1a_hash_48(name: str) -> int:
    """Return a 48-bit truncated FNV-1a hash of the given name."""
    h = FNV_OFFSET64
    for ch in name.encode('utf-8'):
        h ^= ch
        h = (h * FNV_PRIME64) & ((1 << 64) - 1)
    return h & WORD_MASK  # truncate to 48 bits


# opcodes.py: Opcode map and encode/decode
from typing import Tuple
from .encoding import OPR_BITS, OPR_MASK, WORD_MASK, to_tc36

OP = {
    "NOP":          0x000,
    "LOAD_R1":      0x001,
    "LOAD_R2":      0x002,
    "LOAD_R3":      0x003,
    "STORE_R1":     0x004,
    "STORE_R3":     0x005,
    "CLEAR_R1":     0x006,
    "CLEAR_R2":     0x007,
    "CLEAR_R3":     0x008,
    "ADD":          0x009,
    "NEG":          0x00A,
    "MUL":          0x00B,
    "DIV":          0x00C,
    "ROUND":        0x00D,
    "AND":          0x00E,
    "OR":           0x00F,
    "XOR":          0x010,
    "SHIFT_LEFT":   0x011,
    "SHIFT_RIGHT":  0x012,
    "CALL":         0x013,
    "RET":          0x014,
    "WRITE_TAPE":   0x015,
    "READ_CARD":    0x016,
    "SKIP":         0x017,
    "SKIP_IF_ZERO": 0x018,
    "SKIP_IF_NONZERO": 0x019,
    "TXR":          0x01A,
    "HALT":         0x01B,
    # I/O realism extensions
    "REWIND":       0x01C,  # operand: device (0=scratchpad,1=library,2=cards)
    "FF":           0x01D,  # operand: pack(dev,count) -> dev in top 12 bits, count in low 24 bits
    "STATUS":       0x01E,  # operand: device; result -> r3 (position)
    "JUMP":         0x01F,  # operand: signed 36-bit relative offset (or absolute? usually relative in this CPU for SKIP, but TXR is abs. Let's make JUMP absolute like TXR for simplicity or relative?
                            # Wait, SKIP is relative (+2). TXR is absolute.
                            # Standard JUMP usually absolute or relative.
                            # Let's check assembler. If we want to jump to label, assembler handles it.
                            # Let's make it ABSOLUTE address for simplicity in this tape architecture.
                            # Actually, looking at `cpu.py`, TXR takes absolute.
                            # Let's make JUMP absolute.
}

# --- Cross-device scratchpad loads (new) ---
OP.update({
    "SLOAD_R1":     0x020,
    "SLOAD_R2":     0x021,
    "SLOAD_R3":     0x022,
})


# ---- CALL operand packing ----
# bits [35..32] : MODE
#   0x0 : scratchpad ABS address
#   0x1 : library ABS address
#   0x2 : library TOC index
#   0x3 : library NAMEHASH (needs extra 48-bit immediate after CALL)
# bits [31..28] : FLAGS
#   bit0 (0x1): PB_FLAG -> extra 48-bit immediate word holds PB address
# bits [27..0]  : VALUE (28-bit)

CALL_FLAG_PB = 0x1
CALL_MODE_SCRATCH_ABS = 0x0
CALL_MODE_LIB_ABS      = 0x1
CALL_MODE_LIB_IDX      = 0x2
CALL_MODE_LIB_NAME     = 0x3

def pack_call_operand(mode: int, flags: int, value: int) -> int:
    mode  &= 0xF
    flags &= 0xF
    value &= ((1 << 28) - 1)
    return ((mode << 32) | (flags << 28) | value)

def encode_instr(op_name: str, operand: int = 0) -> int:
    op = OP[op_name] & 0xFFF
    return ((op << OPR_BITS) | to_tc36(operand)) & WORD_MASK


def decode_op(bits48: int) -> Tuple[int, int]:
    return ((bits48 >> OPR_BITS) & 0xFFF), (bits48 & OPR_MASK)

# Helpers for FF packing

def pack_ff_operand(dev: int, count: int) -> int:
    dev &= 0xFFF
    count &= (1 << 24) - 1
    return (dev << 24) | count

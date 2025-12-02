import sys
from cpu_sim.core.tape import TapeFile
from cpu_sim.core.encoding import fnv1a_hash_48 as hash_name

def main():
    tape = TapeFile("scratchpad_io.tape")
    words = []
    for i in range(100, 105):
        val = tape.read_bits(i)
        if val is None:
            break
        print(f"{i}: 0x{val:012X}")

    print(f"Hash 'print_u_dec': {hash_name('print_u_dec')}")
    print(f"Hash 'print_dec': {hash_name('print_dec')}")

if __name__ == "__main__":
    main()

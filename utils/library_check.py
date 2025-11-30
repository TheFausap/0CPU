
from cpu_sim.tools.io_realism import TapeDevice
from cpu_sim.core.encoding import WORD_MASK

lib = TapeDevice('lib.tape', sequential_only=True, ms_per_word=0, start_stop_ms=0)
magic = lib.read_bits(0); version = lib.read_bits(1); entry_count = lib.read_bits(2); toc_start = lib.read_bits(3)
print(f"magic=0x{magic:012X}, version={version}, entries={entry_count}, toc_start={toc_start}")

for i in range(entry_count):
    fn_id = lib.read_bits(toc_start + i*4 + 0)
    nameh = lib.read_bits(toc_start + i*4 + 1)
    start = lib.read_bits(toc_start + i*4 + 2)
    length= lib.read_bits(toc_start + i*4 + 3)
    print(f"TOC[{i}] id=0x{fn_id:012X}, namehash=0x{nameh:012X}, start={start}, length={length}")


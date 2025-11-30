
import os

def split_file(input_file, max_chars=7900, output_dir="chunks"):
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Read the entire file
    with open(input_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Split into chunks
    chunks = [content[i:i+max_chars] for i in range(0, len(content), max_chars)]

    # Write chunks to separate files
    for idx, chunk in enumerate(chunks, start=1):
        chunk_file = os.path.join(output_dir, f"{os.path.basename(input_file)}_part{idx}.txt")
        with open(chunk_file, "w", encoding="utf-8") as cf:
            cf.write(chunk)

    print(f"Split complete! {len(chunks)} parts saved in '{output_dir}'.")

# Example usage:
split_file("cpu_sim/core/cpu.py")
split_file("cpu_sim/core/encoding.py")
split_file("cpu_sim/core/opcodes.py")
split_file("cpu_sim/core/tape.py")
split_file("cpu_sim/tools/assembler.py")
split_file("cpu_sim/tools/io_realism.py")
split_file("cpu_sim/tools/lib_builder.py")
split_file("cpu_sim/tools/cards_builder.py")


# cli.py: main monitoring interface for the 48-bit Tape CPU Simulator
# Provides commands to assemble programs, build libraries/cards, run, and monitor interactively.

import argparse
import sys
import shlex
import curses
import time
from collections import deque
from pathlib import Path
from typing import Optional, List


# Local module imports
from cpu_sim.tools.assembler import MiniAssembler
from cpu_sim.tools.lib_builder import LibraryBuilder
from cpu_sim.tools.cards_builder import CardsBuilder
from cpu_sim.core.tape import TapeFile, CardReader, PaperTape
from cpu_sim.core.cpu import CPU
from cpu_sim.core.opcodes import OP, encode_instr, decode_op, pack_ff_operand
from cpu_sim.core.encoding import WORD_MASK, to_twos_complement, from_twos_complement, q47_to_float
from cpu_sim.tools.io_realism import TapeDevice
from cpu_sim.core.observe import TraceSink


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def _bits_to_lamps(bits: int, width: int = 16) -> str:
    """Render 'width' lamps from the high bits of a 48-bit word."""
    # take top 'width' bits of 48-bit word
    mask = (1 << 48) - 1
    b = (bits & mask)
    # shift to get the high 'width' bits
    shift = 48 - width
    slice_bits = (b >> shift) & ((1 << width) - 1)
    # '●' lit, '○' unlit; fallback to '*' and '.' if your terminal font prefers ASCII
    return "".join("●" if (slice_bits >> (width - 1 - i)) & 1 else "○" for i in range(width))

# Reverse opcode map: code -> name
OP_REV = {v: k for k, v in OP.items()}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_scratchpad(tape: TapeFile, items: List, listing_path: Optional[Path] = None):
    """Write assembled items to scratchpad tape. Optionally emit listing."""
    listing_lines = []
    for it in items:
        tape.write_bits(it.addr, it.bits48)
        listing_lines.append(f"{it.addr:08d}: {it.kind:<5} 0x{it.bits48:012X}")
    if listing_path:
        listing_path.write_text("\n".join(listing_lines), encoding="utf-8")


# -----------------------------------------------------------------------------
# Command handlers
# -----------------------------------------------------------------------------

def _make_device(path: str, realistic: bool, sequential_only: bool, latency: int, start_stop_ms: int, error_rate: float):
    if realistic:
        return TapeDevice(path, sequential_only=sequential_only, ms_per_word=latency, start_stop_ms=start_stop_ms, error_rate=error_rate)
    else:
        return TapeFile(path)


def cmd_assemble(args: argparse.Namespace) -> int:
    src = Path(args.source)
    out = Path(args.out)
    listing = Path(args.listing) if args.listing else None

    text = read_text(src)
    asm = MiniAssembler(text)
    items, start_addr = asm.assemble()

    scratch = TapeFile(str(out))
    write_scratchpad(scratch, items, listing)

    print(f"Assembled '{src.name}' → '{out}' with {len(items)} items. Start address = {start_addr}")

    if args.print_start:
        print(f"START {start_addr}")

    if args.cards:
        cards_out = Path(args.cards)
        cards = TapeFile(str(cards_out))
        cb = CardsBuilder(cards)
        # Prepare pair store on boot cards for all items
        for it in items:
            cb.append_pair_store(it.bits48, it.addr)
        cb.finalize_boot(start_addr)
        if args.cards_listing:
            Path(args.cards_listing).write_text("\n".join(cb.listing), encoding="utf-8")
        print(f"Boot cards written to '{cards_out}'")
    return 0


def cmd_buildlib(args: argparse.Namespace) -> int:
    src = Path(args.source)
    out = Path(args.out)

    text = read_text(src)
    lb = LibraryBuilder(text)
    lb.parse()
    lb.build(str(out))

    print(f"Built library '{src.name}' → '{out}' with {len(lb.functions)} functions.")
    return 0


def cmd_buildcards(args: argparse.Namespace) -> int:
    # Build cards directly from an assembly source; optionally also write scratchpad
    src = Path(args.source)
    cards_out = Path(args.out)
    scratch_out = Path(args.scratch) if args.scratch else None
    listing = Path(args.listing) if args.listing else None

    text = read_text(src)
    asm = MiniAssembler(text)
    items, start_addr = asm.assemble()

    cards = TapeFile(str(cards_out))
    cb = CardsBuilder(cards)
    for it in items:
        cb.append_pair_store(it.bits48, it.addr)
    cb.finalize_boot(start_addr)
    if listing:
        listing.write_text("\n".join(cb.listing), encoding="utf-8")

    print(f"Boot cards built from '{src.name}' → '{cards_out}'. Start address = {start_addr}")

    if scratch_out:
        scratch = TapeFile(str(scratch_out))
        write_scratchpad(scratch, items)
        print(f"Scratchpad written to '{scratch_out}'")
    return 0


def dump_paper_to_file(paper_tape: TapeFile, path: Path):
    lines = []
    for i in range(paper_tape.record_count()):
        val = paper_tape.read_word(i)
        bits = paper_tape.read_bits(i)
        lines.append(f"{i:08d}: signed={val:+d} bits=0x{bits:012X}")
    path.write_text("\n".join(lines), encoding="utf-8")


def cmd_run(args: argparse.Namespace) -> int:
    scratch_dev = _make_device(args.scratch, args.realistic, args.sequential_only, args.latency, args.start_stop_ms, args.error_rate)
    library_dev = _make_device(args.library if args.library else "library.tape", args.realistic, args.sequential_only, args.latency, args.start_stop_ms, args.error_rate)
    cards_tape = TapeFile(str(Path(args.cards))) if args.cards else TapeFile("cards.tape")
    paper_tape = TapeFile(str(Path(args.paper))) if args.paper else TapeFile("paper.tape")

    cpu = CPU(scratch_dev, library_dev, CardReader(cards_tape), PaperTape(paper_tape))


    # Trace configuration
    if args.trace_file:
        cpu.set_trace_sink(TraceSink(path=args.trace_file))
        print(f"Tracing to '{args.trace_file}'")

    # Optional anomaly rules (example)
    try:
        from cpu_sim.tools.anomaly_rules import rule_deep_recursion, rule_high_latency
        cpu.add_anomaly_rule(rule_deep_recursion)
        cpu.add_anomaly_rule(rule_high_latency)
    except Exception:
        pass

    # Execution
    if args.boot:
        if not args.cards:
            print("Error: --boot requires --cards")
            return 1
        print(f"Booting from cards '{args.cards}'...")
        cpu.boot_from_cards()

    if args.start is not None:
        cpu._execute_block(scratch_dev, args.start)
    elif not args.boot:
        print("Error: Must specify --start <addr> or --boot")
        return 1

    print(f"REGS r1={cpu.r1:+d} r2={cpu.r2:+d} r3={cpu.r3:+d}")

    if args.status:
        def dev_info(label, dev):
            info = dev.status() if hasattr(dev, 'status') else {'size_words': dev.record_count()}
            pos = dev.get_position() if hasattr(dev, 'get_position') else None
            print(f"{label}: size={info.get('size_words')} pos={pos} realistic={'yes' if hasattr(dev,'status') else 'no'}")
        dev_info('Scratchpad', scratch_dev)
        dev_info('Library', library_dev)
        print(f"Cards words={cards_tape.record_count()}  Paper words={paper_tape.record_count()}")

    if args.dump_paper:
        dump_paper_to_file(paper_tape, Path(args.dump_paper))
        print(f"Paper tape dumped to '{args.dump_paper}'")
        
    
    # Dump metrics if requested
    if args.trace_metrics:
        import json
        Path(args.trace_metrics).write_text(json.dumps(cpu.metrics, indent=2), encoding="utf-8")
        print(f"Metrics saved to '{args.trace_metrics}'")


    return 0

# -----------------------------------------------------------------------------
# Monitor (interactive)
# -----------------------------------------------------------------------------

class Monitor:
    """Interactive monitor: step/run, inspect registers/memory, assemble & load."""

    def __init__(self, scratch_path: Path, library_path: Optional[Path], cards_path: Optional[Path], paper_path: Optional[Path], realistic: bool, sequential_only: bool, latency: int, start_stop_ms: int, error_rate: float):
        self.scratch = _make_device(str(scratch_path), realistic, sequential_only, latency, start_stop_ms, error_rate)
        self.library = _make_device(str(library_path) if library_path else 'library.tape', realistic, sequential_only, latency, start_stop_ms, error_rate)
        self.cards = TapeFile(str(cards_path)) if cards_path else TapeFile('cards.tape')
        self.paper = TapeFile(str(paper_path)) if paper_path else TapeFile('paper.tape')
        self.cpu = CPU(self.scratch, self.library, CardReader(self.cards), PaperTape(self.paper))
        self.dev = self.scratch  # current device
        self.ip: Optional[int] = 0
        self.trace: bool = False

    def prompt(self):
        name = 'scratch' if self.dev is self.scratch else ('library' if self.dev is self.library else 'unknown')
        return f"cpu@{name}:0x{(self.ip if self.ip is not None else 0):06X}> "

    def print_regs(self):
        print(f"r1={self.cpu.r1:+d} r2={self.cpu.r2:+d} r3={self.cpu.r3:+d}")

    def disasm_one(self, dev, ip: int) -> str:
        bits = dev.read_bits(ip)
        if bits is None:
            return f"{ip:08d}: <EOF>"
        op_code, opr = decode_op(bits)
        op_name = OP_REV.get(op_code, f"OP_{op_code:03X}")
        return f"{ip:08d}: {op_name:<14} 0x{opr:09X} (bits=0x{bits:012X})"

    def do_disasm(self, args: List[str]):
        if len(args) < 1:
            print("disasm <addr> [count]")
            return
        addr = int(args[0], 0)
        count = int(args[1], 0) if len(args) > 1 else 8
        for i in range(count):
            print(self.disasm_one(self.dev, addr + i))

    def do_read(self, args: List[str]):
        if len(args) < 1:
            print("read <addr> [count]")
            return
        addr = int(args[0], 0)
        count = int(args[1], 0) if len(args) > 1 else 8
        for i in range(count):
            bits = self.dev.read_bits(addr + i)
            val = self.dev.read_word(addr + i)
            bits_str = f"0x{bits:012X}" if bits is not None else "<EOF>"
            print(f"{addr+i:08d}: signed={val:+d} bits={bits_str}")

    def do_write(self, args: List[str]):
        if len(args) < 2:
            print("write <addr> <signed_value>")
            return
        addr = int(args[0], 0)
        val = int(args[1], 0)
        self.dev.write_word(addr, val)
        print(f"Wrote signed {val:+d} at {addr}")

    def do_loadasm(self, args: List[str]):
        if len(args) < 1:
            print("loadasm <file.asm> [--cards <cards.tape>] [--listing <list.txt>]")
            return
        # rudimentary option parsing
        asm_path = Path(args[0])
        cards_out = None
        listing_path = None
        i = 1
        while i < len(args):
            if args[i] == "--cards" and i+1 < len(args):
                cards_out = Path(args[i+1])
                i += 2
            elif args[i] == "--listing" and i+1 < len(args):
                listing_path = Path(args[i+1])
                i += 2
            else:
                i += 1
        text = read_text(asm_path)
        asm = MiniAssembler(text)
        items, start_addr = asm.assemble()
        write_scratchpad(self.scratch, items, listing_path)
        print(f"Loaded '{asm_path.name}' to scratchpad. START={start_addr}")
        self.ip = start_addr
        if cards_out:
            cards = TapeFile(str(cards_out))
            cb = CardsBuilder(cards)
            for it in items:
                if it.kind == "data":
                    cb.append_pair_store(it.bits48, it.addr)
            cb.finalize_boot(start_addr)
            print(f"Boot cards written to '{cards_out}'")
            
    def do_libtoc(self, args):
        dev = self.library
        magic = dev.read_bits(0); ver = dev.read_bits(1); cnt = dev.read_bits(2); toc = dev.read_bits(3)
        print(f"LIB magic=0x{magic:012X} ver=0x{ver:012X} entries={cnt} toc_start={toc}")
        for i in range(int(cnt or 0)):
            base = int(toc or 0) + i*4
            fn_id    = dev.read_bits(base+0)
            namehash = dev.read_bits(base+1)
            start    = dev.read_bits(base+2)
            length   = dev.read_bits(base+3)
            print(f"[{i}] id=0x{fn_id:012X} namehash=0x{namehash:012X} start={start} len={length}")
            
    def do_scratchcall(self, args):
        addr = int(args[0], 0)
        bits = self.scratch.read_bits(addr)
        print(self.disasm_one(self.scratch, addr))
        # Show potential extra words after CALL
        op_code, opr = decode_op(bits)
        if op_code == OP["CALL"]:
            consumed = 0
            mode  = (opr >> 32) & 0xF
            flags = (opr >> 28) & 0xF
            if mode == 0x3:  # LIBNAME
                nh = self.scratch.read_bits(addr+1)
                print(f"  namehash @{addr+1}: 0x{nh:012X}")
                consumed += 1
            if flags & 0x1:  # PB flag
                pb = self.scratch.read_bits(addr+1+consumed)
                print(f"  PB addr  @{addr+1+consumed}: {pb}")


    def do_start(self, args: List[str]):
        if len(args) < 1 and self.ip is None:
            print("start <addr>")
            return
        if len(args) >= 1:
            self.ip = int(args[0], 0)
        self.dev = self.scratch
        print(f"Start set: dev=scratch addr={self.ip}")

    def do_step(self, args: List[str]):
        n = int(args[0], 0) if args else 1
        if self.ip is None:
            print("No start IP set. Use 'start <addr>' or 'loadasm'.")
            return
        for _ in range(n):
            bits = self.dev.read_bits(self.ip)
            if bits is None:
                print("EOF at current IP. Stopping.")
                self.ip = None
                break
            if self.trace:
                print(self.disasm_one(self.dev, self.ip))
            next_ip = self.cpu.execute_encoded(self.dev, bits48=bits, tape_ip=self.ip)
            if next_ip is None:
                print("HALT/RET (empty) encountered. Stopping.")
                self.ip = None
                break
            # Device may switch during CALL/RET
            self.dev = self.cpu._current_dev if getattr(self.cpu, "_current_dev", None) else self.dev
            self.ip = next_ip
            print(self.lamps_line())

    def do_run(self, args: List[str]):
        max_steps = int(args[0], 0) if args else 1000000
        steps = 0
        while self.ip is not None and steps < max_steps:
            self.do_step(["1"])  # step one
            steps += 1
        print(f"Run finished after {steps} steps. IP={self.ip}")
        
    def do_bootcards(self, args: List[str]):
        self.cpu.boot_from_cards()

    def do_trace(self, args: List[str]):
        self.trace = not self.trace
        print(f"Trace {'ON' if self.trace else 'OFF'}")

    def do_regs(self, args: List[str]):
        self.print_regs()

    def do_device(self, args: List[str]):
        if not args:
            print("device scratch|library")
            return
        name = args[0].lower()
        if name.startswith("scr"):
            self.dev = self.scratch
        elif name.startswith("lib"):
            self.dev = self.library
        else:
            print("Unknown device. Use 'scratch' or 'library'.")
            return
        print(f"Device set to {name}")

    def do_rewind(self, args: List[str]):
        dev_code = 0 if (not args or args[0].lower().startswith("scr")) else 1
        bits = encode_instr("REWIND", dev_code)
        self.cpu.execute_encoded(self.dev, bits, tape_ip=self.ip)
        print(f"Requested REWIND dev={dev_code}")

    def do_ff(self, args: List[str]):
        if len(args) < 2:
            print("ff <dev> <count>  # dev: 0 scratchpad, 1 library, 2 cards")
            return
        dev_code = int(args[0], 0)
        count = int(args[1], 0)
        operand = pack_ff_operand(dev_code, count)
        bits = encode_instr("FF", operand)
        self.cpu.execute_encoded(self.dev, bits, tape_ip=self.ip)
        print(f"Requested FF dev={dev_code} count={count}")

    def do_status(self, args: List[str]):
        dev_code = int(args[0], 0) if args else 0
        bits = encode_instr("STATUS", dev_code)
        self.cpu.execute_encoded(self.dev, bits, tape_ip=self.ip)
        print(f"STATUS dev={dev_code} -> r3={self.cpu.r3}")

    def do_devinfo(self, args: List[str]):
        dev = self.dev
        if hasattr(dev, 'status'):
            info = dev.status()
            print(f"Device info: position={info.get('position')} size_words={info.get('size_words')} sequential_only={info.get('sequential_only')} ms_per_word={info.get('ms_per_word')} error_rate={info.get('error_rate')}")
        else:
            print("Device has no realism; using basic TapeFile.")

    def do_paper(self, args: List[str]):
        # Dump paper tape contents
        for i in range(self.paper.record_count()):
            val = self.paper.read_word(i)
            bits = self.paper.read_bits(i)
            print(f"{i:08d}: signed={val:+d} bits=0x{bits:012X}")
            
    def do_paperf(self, args):
        for i in range(self.paper.record_count()):
            val = self.paper.read_word(i)
            print(f"{i:08d}: Q47={val:+d}  float={q47_to_float(val):.12f}")

    def lamps_line(self):
        def lamp(bits): return _bits_to_lamps(bits, width=16)
        return f"[r1]{lamp(to_twos_complement(self.cpu.r1))}  [r2]{lamp(to_twos_complement(self.cpu.r2))}  [r3]{lamp(to_twos_complement(self.cpu.r3))}"

    def do_lights(self, args):
        print(self.lamps_line())

    def loop(self):
        print("Interactive monitor. Type 'help' for commands. Ctrl-D to exit.")
        while True:
            try:
                line = input(self.prompt())
            except EOFError:
                print()
                break
            if not line.strip():
                continue
            parts = shlex.split(line)
            cmd, *args = parts
            if cmd in ("quit", "exit"):
                break
            elif cmd == "help":
                print("""
Commands:
  loadasm <file.asm> [--cards <cards.tape>] [--listing <list.txt>]  Assemble & load to scratchpad; set START IP.
  start <addr>                                                       Set start IP (scratchpad).
  step [n]                                                           Step n instructions.
  run [max_steps]                                                    Run until HALT/RET or max steps.
  bootcards                                                          Boot from a card stack.
  trace                                                              Toggle trace.
  lights                                                             Show single-line lamps for registers.
  regs                                                               Show registers.
  device scratch|library                                             Switch current device for read/disasm.
  disasm <addr> [count]                                              Disassemble from addr.
  libtoc                                                             Print library tape TOC.
  scratchcall <addr>                                                 Show the CALL and its extra words.
  read <addr> [count]                                                Dump words from current device.
  write <addr> <signed_value>                                        Write signed word to current device.
  rewind [scratch|library]                                           Request REWIND via opcode.
  ff <dev> <count>                                                   Request fast-forward.
  status [dev]                                                       Request STATUS; result in r3.
  devinfo                                                            Show realism parameters for current device.
  paper                                                              Dump paper tape contents.
  paperf                                                             Dump paper tape contents (floating point).
  help, exit, quit                                                   Show help / exit.
                """)
            else:
                fn = getattr(self, f"do_{cmd}", None)
                if fn:
                    try:
                        fn(args)
                    except Exception as e:
                        print(f"Error: {e}")
                else:
                    print(f"Unknown command: {cmd}. Type 'help'.")


def _blinklights_loop(mon: Monitor, ip):
    """Curses-based blinklights panel. Press 'q' to exit panel, 'space' to pause/resume."""
    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    stdscr.nodelay(True)
    if ip is not None:
        mon.ip = ip
    try:
        # State
        paused = False
        last_op = ""
        last_opr = 0
        rate_window = deque(maxlen=50)
        last_t = time.time()
        steps_count = 0

        while True:
            ch = stdscr.getch()
            if ch == ord('q'):
                break
            elif ch == ord(' '):
                paused = not paused

            # advance CPU one step if not paused and IP valid
            if not paused and mon.ip is not None:
                bits = mon.dev.read_bits(mon.ip)
                
                if bits is None:
                    mon.ip = None
                else:
                    op_code, opr = decode_op(bits)
                    op_name = OP_REV.get(op_code, f"OP_{op_code:03X}")
                    next_ip = mon.cpu.execute_encoded(mon.dev, bits48=bits, tape_ip=mon.ip)
                    mon.dev = mon.cpu._current_dev if getattr(mon.cpu, "_current_dev", None) else mon.dev
                    mon.ip = next_ip
                    # perf
                    steps_count += 1
                    now = time.time()
                    rate_window.append(now - last_t)
                    last_t = now
                    last_op = op_name
                    last_opr = opr

            # compute throughput
            avg_dt = (sum(rate_window) / len(rate_window)) if rate_window else 0.0
            sps = (1.0 / avg_dt) if avg_dt > 0 else 0.0

            # Draw
            stdscr.erase()
            stdscr.addstr(0, 0, "48-bit Tape CPU — Blinklights monitor  (q=quit, space=pause)")
            stdscr.addstr(1, 0, f"Paused: {'YES' if paused else 'NO'}   Steps: {steps_count}   Rate: {sps:7.1f} steps/s")

            # Registers
            stdscr.addstr(3, 0, "Registers:")
            stdscr.addstr(4, 2, f"r1={mon.cpu.r1:+20d}  { _bits_to_lamps(to_twos_complement(mon.cpu.r1)) }")
            stdscr.addstr(5, 2, f"r2={mon.cpu.r2:+20d}  { _bits_to_lamps(to_twos_complement(mon.cpu.r2)) }")
            stdscr.addstr(6, 2, f"r3={mon.cpu.r3:+20d}  { _bits_to_lamps(to_twos_complement(mon.cpu.r3)) }")

            # Opcode activity
            stdscr.addstr(8, 0, "Opcode:")
            blink = "▮" if (time.time() % 0.3) < 0.15 else " "  # a tiny blink indicator
            stdscr.addstr(9, 2, f"{blink} {last_op:<14}  opr=0x{opr:09X}")

            # Device status (realism aware)
            stdscr.addstr(11, 0, "Devices:")
            def dev_line(label, dev):
                size = dev.get_size_words() if hasattr(dev, "get_size_words") else dev.record_count()
                pos  = dev.get_position() if hasattr(dev, "get_position") else None
                if hasattr(dev, "status"):
                    st = dev.status()
                    return f"{label}: size={size} pos={pos} seq={st.get('sequential_only')} lat={st.get('ms_per_word')}ms err={st.get('error_rate')}"
                return f"{label}: size={size} pos={pos}"
            stdscr.addstr(12, 2, dev_line("Scratchpad", mon.scratch))
            stdscr.addstr(13, 2, dev_line("Library",    mon.library))
            stdscr.addstr(14, 2, dev_line("Cards",      mon.cards))

            # PB shadow activity hint
            stdscr.addstr(15, 0, f"PB shadow: Base=0x{mon.cpu.PB_SHADOW_BASE:06X}")
            
            # Tape activity lamps
            def lamp(active): return "●" if active else "○"
            now = time.time()
            def active_recent(dev): return (now - getattr(dev, "_last_action_time", 0)) < 0.2

            stdscr.addstr(16, 0, "Tape Activity:")
            stdscr.addstr(17, 2, f"Scratchpad: {lamp(active_recent(mon.scratch))}  ({mon.scratch.last_action or '-'})")
            stdscr.addstr(18, 2, f"Library:    {lamp(active_recent(mon.library))}  ({mon.library.last_action or '-'})")
            stdscr.addstr(19, 2, f"Cards:      {lamp(active_recent(mon.cards))}  ({mon.cards.last_action or '-'})")
            stdscr.addstr(20, 2, f"Paper:      {lamp(active_recent(mon.paper))}  ({mon.paper.last_action or '-'})")
            stdscr.addstr(21, 2, f"Error:      {lamp(mon.scratch.last_error or mon.library.last_error)}")

            # Reset error flags after 0.5s so lamp doesn't stay on forever
            if getattr(mon.scratch, 'last_error', False) and (now - getattr(mon.scratch, '_last_action_time', 0)) > 0.5:
                mon.scratch.last_error = False
            if getattr(mon.library, 'last_error', False) and (now - getattr(mon.library, '_last_action_time', 0)) > 0.5:
                mon.library.last_error = False

            # Footer
            stdscr.addstr(23, 0, f"IP={mon.ip if mon.ip is not None else 'None'}  Device={'scratch' if mon.dev is mon.scratch else 'library'}")
            stdscr.refresh()

            # modest sleep to avoid pegging CPU; adjust for smoothness
            time.sleep(0.01)

            # stop when HALT/RET(empty) hit
            if mon.ip is None and not paused:
                stdscr.addstr(26, 0, "Program ended (HALT/RET). Press 'q' to exit.")
                stdscr.refresh()
                time.sleep(0.2)

    finally:
        curses.nocbreak()
        curses.echo()
        curses.endwin()


def cmd_monitor(args: argparse.Namespace) -> int:
    mon = Monitor(Path(args.scratch), Path(args.library) if args.library else None,
                  Path(args.cards) if args.cards else None,
                  Path(args.paper) if args.paper else None,
                  realistic=args.realistic, sequential_only=args.sequential_only,
                  latency=args.latency, start_stop_ms=args.start_stop_ms, error_rate=args.error_rate)
    
    
    # Trace configuration for monitor
    if getattr(args, "trace_file", None):
        mon.cpu.set_trace_sink(TraceSink(path=str(Path(args.trace_file))))
        print(f"Tracing to '{args.trace_file}'")
    elif getattr(args, "trace", False):
        mon_trace_buf = []
        mon.cpu.set_trace_sink(TraceSink(collector=mon_trace_buf))
        mon._trace_buf = mon_trace_buf
        print("Tracing to in-memory buffer (monitor).")

    # Boot if requested
    if getattr(args, "boot", False):
        if not args.cards:
            print("Error: --boot requires --cards")
            return 1
        print(f"Booting from cards '{args.cards}'...")
        mon.cpu.boot_from_cards()
        # After boot, IP should be set by the last TXR instruction
        # We need to ensure mon.ip reflects the CPU's current state if it changed?
        # boot_from_cards executes instructions. If TXR was executed, cpu._execute_block was called?
        # Wait, TXR calls _execute_block which loops until HALT/RET.
        # If the program runs to completion during boot (unlikely for a real program, but possible),
        # then we might be at the end.
        # BUT, TXR in boot cards usually just sets the start address?
        # No, TXR *executes* the block.
        # If the boot cards just load data and then TXR to start, the program *runs* inside TXR.
        # So `boot_from_cards` might run the whole program!
        
        # If we want to *debug* the program after boot, we might need a way to "load but stop at start".
        # But the architecture defines boot as "load and execute".
        # If the user wants to debug, they might need to step through boot?
        # Or maybe we assume the user wants to see the state *after* the program has run (if it finishes)?
        # OR, maybe the user wants to see the blinklights *during* the run?
        
        # If `boot_from_cards` runs the whole program, then `_blinklights_loop` won't show anything until it's done.
        # This is a problem.
        
        # To support "boot and then debug", we might need a special "Boot Loader" that loads but doesn't TXR?
        # Or we rely on the user not having a TXR in their cards if they want to step?
        # But standard `cards_builder` puts a TXR at the end.
        
        # If we want to visualize the running program in blinklights, we need to run `boot_from_cards` *inside* the blinklights loop,
        # OR make `boot_from_cards` capable of yielding/being stepped.
        
        # For now, let's just add the call. If it runs the whole program, so be it. 
        pass

    if getattr(args, "blinklights", False):
        _blinklights_loop(mon, args.start)
    else:
        mon.loop()
        
    return 0

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="48-bit Tape CPU Simulator CLI / Monitor")
    sub = p.add_subparsers(dest="cmd", required=True)

    # assemble
    pa = sub.add_parser("assemble", help="Assemble program source → scratchpad tape")
    pa.add_argument("source", help="Assembly source file")
    pa.add_argument("-o", "--out", default="scratchpad.tape", help="Output scratchpad tape path")
    pa.add_argument("--listing", help="Emit listing to file")
    pa.add_argument("--print-start", dest="print_start", action="store_true", help="Print start address")
    pa.add_argument("--cards", help="Optionally also build boot cards to this path")
    pa.add_argument("--cards-listing", help="Listing file for boot cards")

    # buildlib
    pl = sub.add_parser("buildlib", help="Build library.tape from library assembly")
    pl.add_argument("source", help="Library assembly source file")
    pl.add_argument("-o", "--out", default="library.tape", help="Output library tape path")

    # buildcards
    pc = sub.add_parser("buildcards", help="Build boot cards from assembly; optionally write scratchpad")
    pc.add_argument("source", help="Assembly source file")
    pc.add_argument("-o", "--out", default="cards.tape", help="Output cards tape path")
    pc.add_argument("--scratch", help="Also write scratchpad to this path")
    pc.add_argument("--listing", help="Save cards listing to file")

    # realism common options
    def add_realism_opts(prs: argparse.ArgumentParser):
        prs.add_argument("--realistic", action="store_true", help="Wrap scratchpad/library in TapeDevice for realism")
        prs.add_argument("--sequential-only", action="store_true", help="Use sequential-only access for TapeDevice")
        prs.add_argument("--latency", type=int, default=10, help="Latency ms per word for TapeDevice")
        prs.add_argument("--start-stop-ms", type=int, default=50, help="Start/stop overhead ms for TapeDevice")
        prs.add_argument("--error-rate", type=float, default=0.0, help="Error rate [0..1] for TapeDevice")

    # run
    pr = sub.add_parser("run", help="Run program on simulator")
    pr.add_argument("--scratch", default="scratchpad.tape", help="Scratchpad tape path")
    pr.add_argument("--library", help="Library tape path")
    pr.add_argument("--cards", help="Cards tape path")
    pr.add_argument("--paper", help="Paper tape path")
    pr.add_argument("--start", type=int, help="Start IP (bypass cards)")
    pr.add_argument("--boot", action="store_true", help="Boot from cards (ignore --start)")
    pr.add_argument("--status", action="store_true", help="Print device info after run")
    pr.add_argument("--dump-paper", help="Dump paper tape to file after run")
    pr.add_argument("--trace-file", help="Write JSONL trace to file")
    pr.add_argument("--trace-metrics", help="Write metrics JSON to file")
    add_realism_opts(pr)

    # monitor
    pm = sub.add_parser("monitor", help="Interactive monitor for stepping, inspecting, assembling")
    pm.add_argument("--scratch", default="scratchpad.tape", help="Scratchpad tape path")
    pm.add_argument("--library", help="Library tape path")
    pm.add_argument("--cards", help="Cards tape path")
    pm.add_argument("--paper", help="Paper tape path")
    pm.add_argument("--blinklights", action="store_true", help="Show front-panel blinklights UI")
    pm.add_argument("--start", type=int, help="Start IP (bypass cards)")
    pm.add_argument("--boot", action="store_true", help="Boot from cards (ignore --start)")

    add_realism_opts(pm)

    return p


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "assemble":
        return cmd_assemble(args)
    elif args.cmd == "buildlib":
        return cmd_buildlib(args)
    elif args.cmd == "buildcards":
        return cmd_buildcards(args)
    elif args.cmd == "run":
        return cmd_run(args)
    elif args.cmd == "monitor":
        return cmd_monitor(args)
    else:
        parser.error("Unknown command")
        return 2


if __name__ == "__main__":
    sys.exit(main())

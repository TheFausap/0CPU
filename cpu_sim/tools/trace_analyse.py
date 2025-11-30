# cpu_sim/tools/trace_analyze.py
import json
import sys
from collections import Counter


def analyze(path: str):
    ops = Counter()
    devs = Counter()
    anomalies = Counter()
    stack_hist = Counter()
    ctx_switches = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            ev = json.loads(line)
            ops[ev.get("op_name", "?")] += 1
            devs[ev.get("device", "?")] += 1
            sd = ev.get("stack_depth", 0)
            stack_hist[sd] += 1
            if ev.get("ctx_switch"):
                ctx_switches += 1
            for a in ev.get("anomalies", []) or []:
                anomalies[a] += 1

    print("Top opcodes:", ops.most_common(10))
    print("By device:", dict(devs))
    print("Context switches:", ctx_switches)
    print("Max stack depth:", max(stack_hist.keys() or [0]))
    print("Anomalies:", anomalies.most_common())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m cpu_sim.tools.trace_analyze <trace.jsonl>")
        sys.exit(2)
    analyze(sys.argv[1])


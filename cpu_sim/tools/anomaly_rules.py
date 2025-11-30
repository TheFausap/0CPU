# tools/anomaly_rules.py
def rule_deep_recursion(event, max_depth=128):
    return ["deep_recursion"] if (event.get("stack_depth", 0) > max_depth) else []

def rule_frequent_ctx_switch(event, state):
    # state is a dict you hold outside to accumulate; here we look at ctx_switch bursts
    hits = []
    if event.get("ctx_switch"):
        state["ctx_switches"] = 1 + state.get("ctx_switches", 0)
        if state["ctx_switches"] > 1000:
            hits.append("ctx_switch_burst")
    return hits

def rule_high_latency(event, threshold_ms=50):
    lat = event.get("latency_ms")
    return ["high_latency"] if (lat is not None and lat > threshold_ms) else []

def rule_operand_out_of_range(event):
    # signed 36-bit operand expected range
    v = event.get("operand_dec")
    if v is None: return []
    return ["operand_oob"] if not (-(1<<35) <= v <= ((1<<35)-1)) else []


# core/observe.py
import json, time
from typing import Optional, Dict, Any

class TraceSink:
    """Simple sink that appends JSON lines to a file path or a list-like collector."""
    def __init__(self, path: Optional[str] = None, collector: Optional[list] = None):
        self.path = path
        self.collector = collector

    def emit(self, event: Dict[str, Any]):
        line = json.dumps(event, separators=(",", ":"))
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        elif self.collector is not None:
            self.collector.append(event)

def now_ts() -> float:
    return time.time()

def safe_int(x, default=None):
    try:
        return int(x)
    except Exception:
        return default


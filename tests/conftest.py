# tests/conftest.py
import sys, os
# Add project root (OCPU/) to sys.path so both `cpu_sim` and `tests.*` are importable
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


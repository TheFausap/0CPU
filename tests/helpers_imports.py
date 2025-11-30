# tests/helpers_imports.py
"""Helper to import modules whether the package is installed as `cpu_sim` or flat files.
Provides `mod` with attributes: core, tools, cli.
"""
import importlib

class _Mod:
    pass

mod = _Mod()

# Try package imports first
try:
    #mod.core = importlib.import_module('cpu_sim.core')
    mod.encoding = importlib.import_module('cpu_sim.core.encoding')
    mod.opcodes = importlib.import_module('cpu_sim.core.opcodes')
    mod.cpu = importlib.import_module('cpu_sim.core.cpu')
    mod.tape = importlib.import_module('cpu_sim.core.tape')
    mod.tools_assembler = importlib.import_module('cpu_sim.tools.assembler')
    mod.tools_lib_builder = importlib.import_module('cpu_sim.tools.lib_builder')
    mod.tools_io = importlib.import_module('cpu_sim.tools.io_realism')
    mod.cli = importlib.import_module('cli')
except Exception:
    # Fallback to local modules
    mod.encoding = importlib.import_module('encoding')
    mod.opcodes = importlib.import_module('opcodes')
    mod.cpu = importlib.import_module('cpu')
    mod.tape = importlib.import_module('tape')
    try:
        mod.tools_assembler = importlib.import_module('assembler')
        mod.tools_lib_builder = importlib.import_module('lib_builder')
        mod.tools_io = importlib.import_module('io_realism')
    except Exception:
        mod.tools_assembler = None
        mod.tools_lib_builder = None
        mod.tools_io = None
    try:
        mod.cli = importlib.import_module('cli')
    except Exception:
        mod.cli = None

"""Load the flat application as a package, including from folders with spaces."""
import hashlib
import importlib
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


def load_package():
    # Copies in different folders must not reuse another copy's modules in Maya.
    name = '_capoeira_app_' + hashlib.sha256(str(ROOT).casefold().encode('utf-8')).hexdigest()[:12]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, str(ROOT / '__init__.py'), submodule_search_locations=[str(ROOT)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        try:
            spec.loader.exec_module(package)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return name


def import_module(name):
    return importlib.import_module('.' + name, load_package())

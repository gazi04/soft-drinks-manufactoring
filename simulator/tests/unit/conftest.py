"""pytest setup: make the helper modules in tests/ (fuxa_shapes.py, …) importable from tests/unit."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

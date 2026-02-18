from __future__ import annotations

import sys
from pathlib import Path

# Make processor/src importable from tests without install
sys.path.insert(0, str(Path(__file__).parent / "src"))

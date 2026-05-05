"""Ensure ``src`` is on ``sys.path`` so ``pytest`` discovers ``agent_runtime`` / ``tool_integration`` / ``feishu_adapter``."""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC.resolve()) not in sys.path:
    sys.path.insert(0, str(_SRC.resolve()))

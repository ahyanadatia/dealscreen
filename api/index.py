"""Vercel / ASGI entrypoint for the dealscreen web app."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dealscreen.web.app import create_app  # noqa: E402

app = create_app(thesis_dir=_ROOT / "theses")

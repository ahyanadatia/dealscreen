from __future__ import annotations

from pathlib import Path

import pytest

from dealscreen.config import Settings
from dealscreen.thesis import Thesis, load_thesis

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def thesis() -> Thesis:
    return load_thesis(ROOT / "theses" / "energy-transition-growth.yaml")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY=None,
        COMPANIES_HOUSE_API_KEY=None,
        DEALSCREEN_DATA_DIR=tmp_path / "data",
        DEALSCREEN_MAX_TURNS=6,
    )


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

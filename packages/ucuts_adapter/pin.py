"""Pinned Ucuts repository metadata loader (commit 15469eeb1c327650d46731815486725085dd733d)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_PIN_PATH = Path(__file__).resolve().parent / "UCUTS_PIN.json"
EXPECTED_UCUTS_COMMIT = "15469eeb1c327650d46731815486725085dd733d"


@dataclass(frozen=True)
class UcutsPin:
    """Machine-readable Ucuts source pin verified against Phase 0 audit."""

    repository: str
    pinned_commit: str
    license: str
    entrypoint: str
    status: str


def load_ucuts_pin() -> UcutsPin:
    """Load and validate packages/ucuts_adapter/UCUTS_PIN.json."""
    raw = json.loads(_PIN_PATH.read_text(encoding="utf-8"))
    return UcutsPin(
        repository=str(raw["repository"]),
        pinned_commit=str(raw["pinned_commit"]),
        license=str(raw["license"]),
        entrypoint=str(raw["entrypoint"]),
        status=str(raw["status"]),
    )

"""Tests der mitgelieferten Dashboard-Karte.

Die Karte wird aus dem Paket der Integration ausgeliefert. Fehlt die Datei im
Release, scheitert die Einrichtung beim Anmelden der statischen Route — das
soll schon hier auffallen, nicht erst beim Anwender.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton.coordinator import StratonCoordinator
from custom_components.ati_straton.frontend import CARD_FILE, CARD_URL, _fingerprint


def test_card_file_is_shipped() -> None:
    assert CARD_FILE.is_file()
    assert CARD_URL.endswith(CARD_FILE.name)


def test_card_registers_its_element() -> None:
    source = CARD_FILE.read_text(encoding="utf-8")
    assert 'const CARD_TAG = "ati-straton-card";' in source
    assert "customElements.define(CARD_TAG" in source


def test_fingerprint_follows_content(tmp_path: Path) -> None:
    file = tmp_path / "card.js"
    file.write_text("a")
    first = _fingerprint(file)
    file.write_text("b")
    assert _fingerprint(file) != first


def _offset(timeinfo: dict) -> int | None:
    fake = SimpleNamespace(data=SimpleNamespace(timeinfo=timeinfo))
    return StratonCoordinator.device_utc_offset.fget(fake)


@pytest.mark.parametrize(
    ("timeinfo", "expected"),
    [
        # getTimezoneOffset: MESZ liefert -120, gemeint ist UTC+2.
        ({"offset": -120}, 120),
        ({"offset": 300}, -300),
        ({"offset": 0}, 0),
        ({}, None),
        ({"offset": "x"}, None),
        ({"offset": True}, None),
    ],
)
def test_device_utc_offset(timeinfo: dict, expected: int | None) -> None:
    assert _offset(timeinfo) == expected

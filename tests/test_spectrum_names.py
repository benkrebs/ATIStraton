"""Tests der ausgeschriebenen Kanalnamen und des Farbpunkt-Endpunkts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton.spectrum import (
    CHANNEL_COLORS,
    CHANNEL_NAMES,
    channel_hex,
    channel_name,
)


class TestChannelNames:
    def test_every_known_channel_has_both_languages(self) -> None:
        for channel, names in CHANNEL_NAMES.items():
            assert set(names) == {"de", "en"}, channel
            assert all(names.values()), channel

    def test_names_cover_every_colour(self) -> None:
        """Kein Kanal darf eine Farbe, aber keinen Namen haben."""
        assert set(CHANNEL_NAMES) == set(CHANNEL_COLORS)

    @pytest.mark.parametrize(
        ("channel", "expected"),
        [
            ("V", "Violett (V)"),
            ("RB", "Royalblau (RB)"),
            ("B", "Blau (B)"),
            ("LC", "Cyan (LC)"),
            ("W", "Weiß (W)"),
            ("R", "Rot (R)"),
        ],
    )
    def test_german_names(self, channel: str, expected: str) -> None:
        assert channel_name(channel, "de") == expected

    def test_english_falls_back_for_unknown_language(self) -> None:
        assert channel_name("V", "fr") == "Violet (V)"

    def test_code_is_always_visible(self) -> None:
        """Der Code steht in Klammern, weil set_color ihn als Schlüssel braucht."""
        for channel in CHANNEL_NAMES:
            assert f"({channel})" in channel_name(channel, "de")

    def test_cyan_shows_lc_not_c(self) -> None:
        """Die Geräteoberfläche beschriftet den Kanal C, der Schlüssel ist LC."""
        assert "(LC)" in channel_name("LC", "de")
        assert "(C)" not in channel_name("LC", "de")

    def test_unknown_channel_returns_the_code(self) -> None:
        assert channel_name("NOPE", "de") == "NOPE"


class TestChannelIconView:
    @pytest.mark.asyncio
    async def test_known_channel_yields_svg_in_its_colour(self) -> None:
        from custom_components.ati_straton.http import StratonChannelIconView

        response = await StratonChannelIconView().get(None, "LC")  # type: ignore[arg-type]
        assert response.status == 200
        assert response.content_type == "image/svg+xml"
        assert channel_hex("LC").encode() in response.body

    @pytest.mark.asyncio
    async def test_unknown_channel_is_rejected(self) -> None:
        """Fremde Zeichenketten dürfen nie in das SVG gelangen."""
        from custom_components.ati_straton.http import StratonChannelIconView

        for bad in ("NOPE", "<script>", "../etc/passwd"):
            response = await StratonChannelIconView().get(None, bad)  # type: ignore[arg-type]
            assert response.status == 404

    @pytest.mark.asyncio
    async def test_every_channel_is_served(self) -> None:
        from custom_components.ati_straton.http import StratonChannelIconView

        for channel in CHANNEL_COLORS:
            response = await StratonChannelIconView().get(None, channel)  # type: ignore[arg-type]
            assert response.status == 200

"""Tests der ausgeschriebenen Kanalnamen und des Farbpunkt-Endpunkts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton.spectrum import (
    CHANNEL_COLORS,
    CHANNEL_NAMES,
    CHANNEL_ORDER,
    channel_hex,
    channel_name,
    channel_position,
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
        assert channel_name(channel, "de", numbered=False) == expected

    def test_english_falls_back_for_unknown_language(self) -> None:
        assert channel_name("V", "fr", numbered=False) == "Violet (V)"

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


class TestOrdering:
    """Home Assistant sortiert alphabetisch; die Ziffer erzwingt die Reihenfolge."""

    EXPECTED = ("V", "B", "RB", "LC", "W", "R")

    def test_positions_follow_the_intended_order(self) -> None:
        assert [channel_position(c) for c in self.EXPECTED] == [1, 2, 3, 4, 5, 6]

    def test_alphabetical_sorting_yields_the_intended_order(self) -> None:
        names = {channel_name(c, "de"): c for c in self.EXPECTED}
        assert tuple(names[n] for n in sorted(names)) == self.EXPECTED

    def test_dropdown_sorts_before_every_channel(self) -> None:
        """„Farbe bearbeiten" muss vor allen Reglern stehen."""
        for channel in self.EXPECTED:
            assert "Farbe bearbeiten" < channel_name(channel, "de")

    def test_guardian_sliders_sort_after_every_channel(self) -> None:
        for channel in self.EXPECTED:
            assert channel_name(channel, "de") < "Wächter Abregeltemperatur"

    def test_english_order_matches_too(self) -> None:
        names = {channel_name(c, "en"): c for c in self.EXPECTED}
        assert tuple(names[n] for n in sorted(names)) == self.EXPECTED

    def test_every_named_channel_has_a_position(self) -> None:
        assert set(CHANNEL_ORDER) == set(CHANNEL_NAMES)

    def test_unknown_channel_sorts_last(self) -> None:
        assert channel_position("NOPE") > len(CHANNEL_ORDER)


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

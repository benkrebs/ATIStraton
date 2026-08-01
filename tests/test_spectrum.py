"""Tests der Farbnäherung für die Darstellung."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton.spectrum import (
    NEUTRAL,
    channel_hex,
    mix_hex,
    mix_rgb,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(name="colors")
def colors_fixture() -> list[dict]:
    return json.loads((FIXTURES / "colors.json").read_text())


class TestChannelColours:
    @pytest.mark.parametrize("channel", ["V", "RB", "B", "LC", "W", "R"])
    def test_known_channels_have_a_colour(self, channel: str) -> None:
        value = channel_hex(channel)
        assert value.startswith("#") and len(value) == 7
        assert value != NEUTRAL

    def test_unknown_channel_falls_back(self) -> None:
        assert channel_hex("NOPE") == NEUTRAL


class TestMixing:
    def test_every_device_colour_yields_a_value(self, colors: list[dict]) -> None:
        for color in colors:
            composition = {v["name"]: v["value"] for v in color["values"]}
            assert mix_hex(composition) is not None

    def test_pure_red_stays_red(self) -> None:
        assert mix_rgb({"R": 255}) == (255, 30, 0)

    def test_all_zero_has_no_colour(self) -> None:
        assert mix_rgb({"W": 0, "B": 0}) is None
        assert mix_hex({}) is None

    def test_unknown_channels_are_ignored(self) -> None:
        assert mix_rgb({"NOPE": 255, "R": 255}) == mix_rgb({"R": 255})

    def test_result_is_always_full_scale(self, colors: list[dict]) -> None:
        """Ohne Normierung erschiene jede Mischung dunkelgrau."""
        for color in colors:
            composition = {v["name"]: v["value"] for v in color["values"]}
            rgb = mix_rgb(composition)
            assert rgb is not None
            assert max(rgb) == 255

    def test_components_stay_in_range(self, colors: list[dict]) -> None:
        for color in colors:
            composition = {v["name"]: v["value"] for v in color["values"]}
            assert all(0 <= c <= 255 for c in mix_rgb(composition))

    def test_white_heavy_mix_is_lighter_than_pure_blue(self) -> None:
        """Eine weißlastige Mischung muss heller herauskommen als eine rein blaue."""
        fluorescent = mix_rgb({"V": 150, "RB": 255, "B": 140})
        natural = mix_rgb({"V": 110, "RB": 60, "B": 111, "LC": 255, "W": 255, "R": 25})
        assert sum(natural) > sum(fluorescent)

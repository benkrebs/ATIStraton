"""Tests des Sicherheitslayers gegen die echten Gerätedaten aus Phase 0.

Das Modul ist sicherheitskritisch: ein Fehler hier kann die LEDs überfahren.
Die Tests laufen ohne Home-Assistant-Abhängigkeiten.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton.limits import (
    ABSOLUTE_MAX,
    ChannelLimitError,
    ChannelLimits,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Am Gerät verifizierte Hardware-Grenzen (identisch über alle drei Spots).
EXPECTED_CEILINGS = {"W": 116, "V": 49, "RB": 57, "B": 118, "LC": 59, "R": 15}


def _fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(name="limits")
def limits_fixture() -> ChannelLimits:
    return ChannelLimits.from_device(_fixture("spots"), _fixture("colors"))


class TestDerivationFromDevice:
    """S1/S2 - Ableitung der Deckel aus den Gerätedaten."""

    def test_ceilings_match_verified_hardware_limits(
        self, limits: ChannelLimits
    ) -> None:
        assert dict(limits.ceilings) == EXPECTED_CEILINGS

    def test_all_six_channels_present(self, limits: ChannelLimits) -> None:
        assert set(limits.channels) == set(EXPECTED_CEILINGS)

    def test_red_channel_stays_usable_despite_missing_max(
        self, limits: ChannelLimits
    ) -> None:
        """Der vierte R-Kanal hat kein max; R bleibt trotzdem nutzbar (S2)."""
        assert limits.effective_ceiling("R") == 15

    def test_ceiling_is_the_more_conservative_of_both_domains(
        self, limits: ChannelLimits
    ) -> None:
        """Presets erreichen bis 255, die Hardware-Grenze ist niedriger."""
        for channel, ceiling in limits.ceilings.items():
            assert ceiling <= EXPECTED_CEILINGS[channel]
            assert ceiling < ABSOLUTE_MAX

    def test_channel_without_any_known_max_is_disabled(self) -> None:
        spots = [{"channels": [{"name": "X", "channel": 1}]}]
        limits = ChannelLimits.from_device(spots, [])
        assert limits.ceilings["X"] == 0
        with pytest.raises(ChannelLimitError, match="gesperrt"):
            limits.clamp("X", 1)

    def test_empty_device_data_yields_no_channels(self) -> None:
        limits = ChannelLimits.from_device([], [])
        assert limits.channels == ()
        with pytest.raises(ChannelLimitError, match="Unbekannter Kanal"):
            limits.clamp("W", 1)


class TestClampValidation:
    """S0 - Typ- und Bereichsprüfung."""

    @pytest.mark.parametrize("bad", [None, "42", 4.2, b"1", [], {}])
    def test_non_int_is_rejected(self, limits: ChannelLimits, bad: object) -> None:
        with pytest.raises(ChannelLimitError, match="muss int sein"):
            limits.clamp("W", bad)

    def test_bool_is_rejected(self, limits: ChannelLimits) -> None:
        """bool ist Subtyp von int und darf nicht als 1 durchrutschen."""
        with pytest.raises(ChannelLimitError, match="muss int sein"):
            limits.clamp("W", True)

    @pytest.mark.parametrize("bad", [-1, 256, 1000, -1000])
    def test_out_of_range_is_rejected(self, limits: ChannelLimits, bad: int) -> None:
        with pytest.raises(ChannelLimitError, match="außerhalb"):
            limits.clamp("W", bad)

    def test_unknown_channel_is_rejected(self, limits: ChannelLimits) -> None:
        with pytest.raises(ChannelLimitError, match="Unbekannter Kanal"):
            limits.clamp("NOPE", 10)


class TestClampBehaviour:
    """S1 - tatsächliches Klemmen."""

    def test_value_within_ceiling_passes_unchanged(self, limits: ChannelLimits) -> None:
        assert limits.clamp("W", 100) == 100

    def test_value_above_ceiling_is_clamped(self, limits: ChannelLimits) -> None:
        assert limits.clamp("W", 255) == 116

    def test_red_channel_clamps_hard(self, limits: ChannelLimits) -> None:
        """Der gefährlichste Fall: 255 auf R wäre Faktor 17 über der Grenze."""
        assert limits.clamp("R", 255) == 15

    def test_zero_is_always_allowed(self, limits: ChannelLimits) -> None:
        for channel in limits.channels:
            assert limits.clamp(channel, 0) == 0

    def test_every_channel_clamps_max_input_to_its_ceiling(
        self, limits: ChannelLimits
    ) -> None:
        for channel, ceiling in EXPECTED_CEILINGS.items():
            assert limits.clamp(channel, ABSOLUTE_MAX) == ceiling

    def test_no_output_ever_exceeds_its_ceiling(self, limits: ChannelLimits) -> None:
        """Erschöpfend über den gesamten Eingaberaum."""
        for channel, ceiling in EXPECTED_CEILINGS.items():
            for value in range(ABSOLUTE_MAX + 1):
                assert limits.clamp(channel, value) <= ceiling


class TestSafetyFactor:
    """S3 - globaler Sicherheitsfaktor."""

    def test_factor_reduces_ceiling(self, limits: ChannelLimits) -> None:
        halved = limits.with_safety_factor(0.5)
        assert halved.effective_ceiling("W") == 58
        assert halved.clamp("W", 255) == 58

    def test_factor_out_of_range_is_rejected(self, limits: ChannelLimits) -> None:
        for bad in (0.0, 0.05, 1.5, -1.0):
            with pytest.raises(ChannelLimitError, match="safety_factor"):
                limits.with_safety_factor(bad)

    def test_factor_driving_ceiling_to_zero_blocks_writes(
        self, limits: ChannelLimits
    ) -> None:
        """R hat Deckel 15; Faktor 0.1 ergibt 1 (int), 0.1 auf einen 5er-Deckel ergibt 0."""
        tiny = ChannelLimits(ceilings={"R": 5}, safety_factor=0.1)
        assert tiny.effective_ceiling("R") == 0
        with pytest.raises(ChannelLimitError, match="gesperrt"):
            tiny.clamp("R", 1)


class TestBrightnessScaling:
    """Abbildung HA-brightness -> Kanalwert."""

    def test_full_brightness_reaches_exactly_the_ceiling(
        self, limits: ChannelLimits
    ) -> None:
        for channel, ceiling in EXPECTED_CEILINGS.items():
            assert limits.scale_brightness(channel, ABSOLUTE_MAX) == ceiling

    def test_zero_brightness_is_zero(self, limits: ChannelLimits) -> None:
        for channel in limits.channels:
            assert limits.scale_brightness(channel, 0) == 0

    def test_scaling_never_exceeds_ceiling(self, limits: ChannelLimits) -> None:
        for channel, ceiling in EXPECTED_CEILINGS.items():
            for brightness in range(ABSOLUTE_MAX + 1):
                assert 0 <= limits.scale_brightness(channel, brightness) <= ceiling

    def test_scaling_is_monotonic(self, limits: ChannelLimits) -> None:
        for channel in limits.channels:
            previous = -1
            for brightness in range(ABSOLUTE_MAX + 1):
                current = limits.scale_brightness(channel, brightness)
                assert current >= previous
                previous = current

    def test_red_channel_resolution_is_documented_as_lossy(
        self, limits: ChannelLimits
    ) -> None:
        """Deckel 15 laesst nur 16 unterscheidbare Stufen zu."""
        distinct = {limits.scale_brightness("R", b) for b in range(ABSOLUTE_MAX + 1)}
        assert len(distinct) == 16

    @pytest.mark.parametrize("bad", [None, "255", 4.2, True, -1, 256])
    def test_invalid_brightness_is_rejected(
        self, limits: ChannelLimits, bad: object
    ) -> None:
        with pytest.raises(ChannelLimitError):
            limits.scale_brightness("W", bad)


class TestImmutability:
    """Die Grenzen dürfen zur Laufzeit nicht verändert werden können."""

    def test_limits_are_frozen(self, limits: ChannelLimits) -> None:
        with pytest.raises((AttributeError, TypeError)):
            limits.safety_factor = 0.5  # type: ignore[misc]

    def test_construction_rejects_out_of_range_ceiling(self) -> None:
        with pytest.raises(ChannelLimitError, match="außerhalb"):
            ChannelLimits(ceilings={"W": 300})

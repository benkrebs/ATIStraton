"""Tests der Intensitätsskalierung.

Die Testdaten sind frei erfunden (siehe ``fixtures/README.md``). Der Bezugswert
ist ``maxValueOrg = 80``, die Knoten stehen auf ``value = valueOrg × 50 / 80``.
Die Erwartungswerte sind daraus von Hand nach der Formel
``value = valueOrg × n / maxValueOrg`` gerechnet, also unabhängig von der
Implementierung.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton.intensity import (
    IntensityError,
    current_intensity,
    intensity_at,
    max_value_org,
    node_values,
    rescaled_by_factor,
    scaled_timelines,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(name="timelines")
def timelines_fixture() -> list[dict]:
    return json.loads((FIXTURES / "timelines.json").read_text())


class TestReferenceValues:
    def test_max_value_org_matches_device(self, timelines: list[dict]) -> None:
        assert max_value_org(timelines) == 80.0

    def test_current_intensity_is_the_peak(self, timelines: list[dict]) -> None:
        assert current_intensity(timelines) == 50.0

    def test_empty_input_is_zero(self) -> None:
        assert max_value_org([]) == 0.0
        assert current_intensity([]) == 0.0


class TestScaling:
    def test_matches_the_formula_by_hand(self, timelines: list[dict]) -> None:
        """Reglerstellung 40 bei Bezug 80 heißt: jeder Knoten auf valueOrg / 2."""
        scaled = scaled_timelines(timelines, 40.0)
        originals = [float(n["valueOrg"]) for tl in timelines for n in tl["nodes"]]
        assert node_values(scaled) == [round(v / 2, 2) for v in originals]
        assert max(node_values(scaled)) == 40.0

    def test_halving_the_setting_halves_every_node(self, timelines: list[dict]) -> None:
        full = node_values(scaled_timelines(timelines, 80.0))
        half = node_values(scaled_timelines(timelines, 40.0))
        assert half == [round(v / 2, 2) for v in full]

    def test_full_intensity_reproduces_value_org(self, timelines: list[dict]) -> None:
        scaled = scaled_timelines(timelines, max_value_org(timelines))
        originals = [float(n["valueOrg"]) for tl in timelines for n in tl["nodes"]]
        assert node_values(scaled) == originals

    def test_zero_intensity_turns_everything_off(self, timelines: list[dict]) -> None:
        assert set(node_values(scaled_timelines(timelines, 0.0))) == {0.0}

    def test_value_org_is_never_modified(self, timelines: list[dict]) -> None:
        before = [float(n["valueOrg"]) for tl in timelines for n in tl["nodes"]]
        scaled = scaled_timelines(timelines, 20.0)
        after = [float(n["valueOrg"]) for tl in scaled for n in tl["nodes"]]
        assert after == before

    def test_input_is_not_mutated(self, timelines: list[dict]) -> None:
        before = node_values(timelines)
        scaled_timelines(timelines, 10.0)
        assert node_values(timelines) == before

    def test_scaling_is_reversible(self, timelines: list[dict]) -> None:
        original = current_intensity(timelines)
        dimmed = scaled_timelines(timelines, 20.0)
        restored = scaled_timelines(dimmed, original)
        assert node_values(restored) == node_values(
            scaled_timelines(timelines, original)
        )

    @pytest.mark.parametrize("bad", [-1.0, 100.1, 1000.0, True, "50", None])
    def test_invalid_intensity_is_rejected(
        self, timelines: list[dict], bad: object
    ) -> None:
        with pytest.raises(IntensityError):
            scaled_timelines(timelines, bad)

    def test_missing_value_org_is_refused(self) -> None:
        with pytest.raises(IntensityError, match="valueOrg"):
            scaled_timelines([{"nodes": [{"value": 10}]}], 50.0)

    def test_nodes_without_anchor_stay_untouched(self) -> None:
        timelines = [{"nodes": [{"value": 10, "valueOrg": 80}, {"value": 42}]}]
        scaled = scaled_timelines(timelines, 40.0)
        assert scaled[0]["nodes"][0]["value"] == 40.0
        assert scaled[0]["nodes"][1]["value"] == 42


class TestFactorRescaling:
    """Pfad des Temperaturwächters — arbeitet auf den Ist-Werten."""

    def test_factor_scales_current_values(self, timelines: list[dict]) -> None:
        reduced = rescaled_by_factor(timelines, 0.45)
        assert node_values(reduced) == [
            round(v * 0.45, 3) for v in node_values(timelines)
        ]

    def test_factor_one_is_a_noop(self, timelines: list[dict]) -> None:
        assert node_values(rescaled_by_factor(timelines, 1.0)) == node_values(timelines)

    def test_anomalous_nodes_are_not_normalised(self, timelines: list[dict]) -> None:
        """Knoten mit value == valueOrg dürfen nicht auf die Formel gezogen werden."""
        anomalous = [
            (float(n["value"]), float(n["valueOrg"]))
            for tl in timelines
            for n in tl["nodes"]
            if n.get("valueOrg") and float(n["value"]) == float(n["valueOrg"])
        ]
        assert anomalous, "Fixture sollte anomale Knoten enthalten"
        reduced = rescaled_by_factor(timelines, 0.5)
        for tl in reduced:
            for node in tl["nodes"]:
                if node.get("valueOrg"):
                    assert node["valueOrg"] == pytest.approx(float(node["valueOrg"]))

    @pytest.mark.parametrize("bad", [-0.1, 1.1, 2.0])
    def test_invalid_factor_is_rejected(
        self, timelines: list[dict], bad: float
    ) -> None:
        with pytest.raises(IntensityError):
            rescaled_by_factor(timelines, bad)


class TestIntensityAt:
    """Interpolation der Tageskurve auf eine Uhrzeit."""

    def test_peak_at_its_node(self, timelines: list[dict]) -> None:
        """Der höchste Stützpunkt liegt um 11:15 bei 50."""
        assert intensity_at(timelines, 11 * 3600 + 15 * 60) == 50.0

    def test_night_is_dark(self, timelines: list[dict]) -> None:
        assert intensity_at(timelines, 3 * 3600) == 0.0
        assert intensity_at(timelines, 23 * 3600) == 0.0

    def test_interpolates_between_nodes(self, timelines: list[dict]) -> None:
        """09:00 steht auf 0, 10:00 auf 25 — 09:30 muss genau dazwischen liegen."""
        value = intensity_at(timelines, 9 * 3600 + 1800)
        assert 0 < value < 25
        assert value == pytest.approx(12.5, abs=0.01)

    def test_exact_node_times_return_the_node_value(
        self, timelines: list[dict]
    ) -> None:
        assert intensity_at(timelines, 10 * 3600) == 25.0

    def test_wraps_around_midnight(self, timelines: list[dict]) -> None:
        assert intensity_at(timelines, 86400) == intensity_at(timelines, 0)
        assert intensity_at(timelines, 86400 + 3600) == intensity_at(timelines, 3600)

    def test_never_exceeds_the_curve(self, timelines: list[dict]) -> None:
        peak = current_intensity(timelines)
        for second in range(0, 86400, 60):
            assert 0 <= intensity_at(timelines, second) <= peak

    def test_empty_input_is_none(self) -> None:
        assert intensity_at([], 0) is None
        assert intensity_at([{"nodes": []}], 0) is None

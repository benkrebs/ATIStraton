"""Tests der Hysteresesteuerung des Temperaturwächters."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton.guardian import (
    GuardianConfig,
    GuardianState,
    TemperatureGuardian,
)

STEP = 60.0


def _config(**changes) -> GuardianConfig:
    base = {
        "enabled": True,
        "max_temp": 50.0,
        "low_temp": 45.0,
        "reduction_step": 10.0,
        "step_interval": STEP,
    }
    return GuardianConfig(**{**base, **changes})


@pytest.fixture(name="guard")
def guard_fixture() -> TemperatureGuardian:
    return TemperatureGuardian(_config())


class TestDisabled:
    def test_disabled_guard_never_engages(self) -> None:
        guard = TemperatureGuardian(_config(enabled=False))
        decision = guard.evaluate(80.0, 0.0)
        assert decision.state is GuardianState.DISABLED
        assert decision.level == 0.0
        assert not decision.engaged

    def test_disabling_releases_an_active_reduction(
        self, guard: TemperatureGuardian
    ) -> None:
        guard.evaluate(55.0, 0.0)
        assert guard.engaged
        guard.update_config(enabled=False)
        decision = guard.evaluate(55.0, STEP * 5)
        assert decision.level == 0.0
        assert decision.state is GuardianState.DISABLED


class TestReduction:
    def test_first_reading_above_threshold_reduces_immediately(
        self, guard: TemperatureGuardian
    ) -> None:
        decision = guard.evaluate(50.0, 0.0)
        assert decision.state is GuardianState.REDUCING
        assert decision.level == 10.0
        assert decision.factor == pytest.approx(0.9)

    def test_reduction_is_rate_limited(self, guard: TemperatureGuardian) -> None:
        guard.evaluate(55.0, 0.0)
        # Innerhalb des Intervalls darf kein weiterer Schritt erfolgen.
        for offset in (1.0, 30.0, 59.0):
            assert guard.evaluate(55.0, offset).level == 10.0
        assert guard.evaluate(55.0, STEP).level == 20.0

    def test_reduction_caps_at_hundred_percent(
        self, guard: TemperatureGuardian
    ) -> None:
        now = 0.0
        for _ in range(30):
            guard.evaluate(70.0, now)
            now += STEP
        assert guard.level == 100.0
        assert guard.evaluate(70.0, now).factor == 0.0

    def test_threshold_is_inclusive(self, guard: TemperatureGuardian) -> None:
        assert guard.evaluate(50.0, 0.0).state is GuardianState.REDUCING


class TestHysteresis:
    def test_band_between_thresholds_holds_the_level(
        self, guard: TemperatureGuardian
    ) -> None:
        guard.evaluate(55.0, 0.0)
        decision = guard.evaluate(47.0, STEP)
        assert decision.state is GuardianState.HOLDING
        assert decision.level == 10.0

    def test_band_without_prior_reduction_is_idle(
        self, guard: TemperatureGuardian
    ) -> None:
        decision = guard.evaluate(47.0, 0.0)
        assert decision.state is GuardianState.IDLE
        assert decision.level == 0.0

    def test_no_oscillation_at_the_upper_threshold(
        self, guard: TemperatureGuardian
    ) -> None:
        """Pendeln um max_temp darf die Reduktion nicht takten lassen."""
        now = 0.0
        guard.evaluate(50.0, now)
        levels = set()
        for temp in (49.9, 50.0, 49.5, 49.9, 48.0):
            now += STEP
            levels.add(guard.evaluate(temp, now).level)
        # Nur die 50.0 loest einen weiteren Schritt aus, der Rest haelt.
        assert levels == {10.0, 20.0}


class TestRecovery:
    def test_cooling_below_low_temp_recovers_stepwise(
        self, guard: TemperatureGuardian
    ) -> None:
        now = 0.0
        for _ in range(3):
            guard.evaluate(55.0, now)
            now += STEP
        assert guard.level == 30.0

        now += STEP
        assert guard.evaluate(40.0, now).state is GuardianState.RECOVERING
        assert guard.level == 20.0

    def test_full_recovery_returns_to_idle(self, guard: TemperatureGuardian) -> None:
        guard.evaluate(55.0, 0.0)
        decision = guard.evaluate(40.0, STEP)
        assert decision.level == 0.0
        assert decision.state is GuardianState.IDLE
        assert not decision.engaged

    def test_recovery_never_goes_below_zero(self, guard: TemperatureGuardian) -> None:
        now = 0.0
        for _ in range(10):
            guard.evaluate(30.0, now)
            now += STEP
        assert guard.level == 0.0

    def test_recovery_is_rate_limited(self, guard: TemperatureGuardian) -> None:
        now = 0.0
        for _ in range(3):
            guard.evaluate(55.0, now)
            now += STEP
        level_before = guard.level
        assert guard.evaluate(40.0, now - STEP + 1.0).level == level_before


class TestSafety:
    def test_missing_measurement_holds_the_level(
        self, guard: TemperatureGuardian
    ) -> None:
        guard.evaluate(55.0, 0.0)
        decision = guard.evaluate(None, STEP * 3)
        assert decision.level == 10.0
        assert not decision.changed

    def test_missing_measurement_never_starts_a_reduction(
        self, guard: TemperatureGuardian
    ) -> None:
        decision = guard.evaluate(None, 0.0)
        assert decision.level == 0.0

    @pytest.mark.parametrize(
        "changes",
        [
            {"low_temp": 55.0},  # low ueber max
            {"low_temp": 49.5},  # Hysterese zu schmal
            {"max_temp": 90.0},  # ausserhalb des zulaessigen Bereichs
            {"reduction_step": 0.0},
            {"reduction_step": 80.0},
        ],
    )
    def test_invalid_config_keeps_the_guard_inactive(self, changes: dict) -> None:
        guard = TemperatureGuardian(_config(**changes))
        assert not guard.config.valid
        decision = guard.evaluate(80.0, 0.0)
        assert decision.level == 0.0

    def test_invalid_config_holds_an_existing_reduction(
        self, guard: TemperatureGuardian
    ) -> None:
        """Ein Konfigurationsfehler darf die Leuchte nicht schlagartig aufreissen."""
        guard.evaluate(55.0, 0.0)
        guard.update_config(low_temp=55.0)
        decision = guard.evaluate(55.0, STEP * 2)
        assert decision.level == 10.0

    def test_factor_is_always_within_unit_interval(
        self, guard: TemperatureGuardian
    ) -> None:
        now = 0.0
        for temp in (70.0, 70.0, 70.0, 40.0, 55.0, 47.0, 30.0):
            decision = guard.evaluate(temp, now)
            assert 0.0 <= decision.factor <= 1.0
            now += STEP


class TestConfigValidation:
    def test_default_config_is_valid(self) -> None:
        assert _config().valid

    def test_minimum_hysteresis_is_enforced(self) -> None:
        assert _config(max_temp=50.0, low_temp=49.0).valid
        assert not _config(max_temp=50.0, low_temp=49.5).valid

"""Temperaturwächter: senkt die Intensität bei Überhitzung, hebt sie gekühlt wieder an.

Reine Zustandslogik ohne Home-Assistant- und Netzwerkabhängigkeiten, damit die
Hysterese isoliert testbar ist. Die Anwendung auf das Gerät übernimmt der
Coordinator.

Sicherheitsargument: Der Wächter schreibt ausschließlich Werte **kleiner oder
gleich** dem Snapshot dessen, was das Gerät ohnehin schon ausgegeben hat. Er kann
die Leuchte damit nie heller machen als der Zeitplan sie ohnehin fährt — und ist
deshalb unabhängig von der offenen Frage O5a gefahrlos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import StrEnum

_LOGGER = logging.getLogger(__name__)

# Mindestabstand zwischen zwei Regelschritten. Das Gerät sendet alle zwei
# Sekunden Temperaturen; ohne diese Sperre wäre die volle Reduktion in Sekunden
# erreicht und die Regelung würde schwingen.
#
# Fünf Minuten statt einer: Jeder Regelschritt bedeutet ein ``PUT /api/data``,
# also einen Schreibvorgang auf den Flash-Speicher des Geräts. Ein Minutentakt
# ergäbe über 500 000 Schreibzyklen im Jahr. Thermische Vorgänge an einem
# Aquarium laufen ohnehin träge ab.
DEFAULT_STEP_INTERVAL = 300.0

DEFAULT_MAX_TEMP = 50.0
DEFAULT_LOW_TEMP = 45.0
DEFAULT_REDUCTION_STEP = 10.0

MIN_TEMP_SETTING = 25.0
MAX_TEMP_SETTING = 60.0
MIN_HYSTERESIS = 1.0

MIN_REDUCTION_STEP = 1.0
MAX_REDUCTION_STEP = 50.0
MAX_REDUCTION_LEVEL = 100.0


class GuardianState(StrEnum):
    """Betriebszustand des Wächters."""

    DISABLED = "disabled"
    IDLE = "idle"
    REDUCING = "reducing"
    HOLDING = "holding"
    RECOVERING = "recovering"


@dataclass(frozen=True, slots=True)
class GuardianConfig:
    """Einstellbare Parameter des Wächters."""

    enabled: bool = False
    max_temp: float = DEFAULT_MAX_TEMP
    low_temp: float = DEFAULT_LOW_TEMP
    reduction_step: float = DEFAULT_REDUCTION_STEP
    step_interval: float = DEFAULT_STEP_INTERVAL

    @property
    def valid(self) -> bool:
        """Prüft die Parameter auf Plausibilität.

        Ein ungültiger Satz schaltet den Wächter ab, statt zu raten — die
        geräteeigene Grenze (``info.maxTemperature``) bleibt als Rückfallebene.
        """
        return (
            MIN_TEMP_SETTING <= self.low_temp <= MAX_TEMP_SETTING
            and MIN_TEMP_SETTING <= self.max_temp <= MAX_TEMP_SETTING
            and self.max_temp - self.low_temp >= MIN_HYSTERESIS
            and MIN_REDUCTION_STEP <= self.reduction_step <= MAX_REDUCTION_STEP
            and self.step_interval > 0
        )


@dataclass(frozen=True, slots=True)
class GuardianDecision:
    """Ergebnis einer Auswertung."""

    level: float
    state: GuardianState
    changed: bool

    @property
    def factor(self) -> float:
        """Multiplikator auf die Ausgangswerte, 0.0 bis 1.0."""
        return max(0.0, (100.0 - self.level) / 100.0)

    @property
    def engaged(self) -> bool:
        """True, solange eine Reduktion aktiv ist."""
        return self.level > 0.0


class TemperatureGuardian:
    """Hysteresegeregelte Absenkung der Intensität.

    Oberhalb von ``max_temp`` wird die Reduktion schrittweise erhöht, unterhalb
    von ``low_temp`` schrittweise zurückgenommen. Dazwischen bleibt sie stehen —
    das ist die Hysterese, die ein Takten an der Schwelle verhindert.
    """

    def __init__(self, config: GuardianConfig | None = None) -> None:
        self._config = config or GuardianConfig()
        self._level = 0.0
        self._state = GuardianState.DISABLED
        self._last_step: float | None = None

    @property
    def config(self) -> GuardianConfig:
        return self._config

    @property
    def level(self) -> float:
        return self._level

    @property
    def state(self) -> GuardianState:
        return self._state

    @property
    def engaged(self) -> bool:
        return self._level > 0.0

    def update_config(self, **changes: object) -> GuardianConfig:
        """Ändert einzelne Parameter und meldet den neuen Satz zurück."""
        self._config = replace(self._config, **changes)  # type: ignore[arg-type]
        if not self._config.enabled:
            self._reset()
        return self._config

    def _reset(self) -> None:
        self._level = 0.0
        self._state = GuardianState.DISABLED
        self._last_step = None

    def evaluate(self, temperature: float | None, now: float) -> GuardianDecision:
        """Wertet eine Temperaturmeldung aus und liefert die neue Reduktion.

        Args:
            temperature: Höchste gemeldete Spot-Temperatur, oder ``None``, wenn
                keine Messung vorliegt.
            now: Monotone Zeitbasis in Sekunden.
        """
        config = self._config

        if not config.enabled:
            if self._level or self._state is not GuardianState.DISABLED:
                self._reset()
                return GuardianDecision(0.0, GuardianState.DISABLED, changed=True)
            return GuardianDecision(0.0, GuardianState.DISABLED, changed=False)

        if not config.valid:
            _LOGGER.error(
                "Wächter-Konfiguration ungültig (max_temp=%s, low_temp=%s, "
                "reduction_step=%s); Wächter bleibt untätig",
                config.max_temp,
                config.low_temp,
                config.reduction_step,
            )
            if self._level:
                # Bestehende Reduktion halten statt sprunghaft freizugeben.
                return GuardianDecision(self._level, self._state, changed=False)
            self._state = GuardianState.IDLE
            return GuardianDecision(0.0, GuardianState.IDLE, changed=False)

        if temperature is None:
            # Ohne Messwert nichts verändern: eine bestehende Reduktion bleibt
            # bestehen (sichere Richtung), eine neue wird nicht begonnen.
            return GuardianDecision(self._level, self._state, changed=False)

        previous_level = self._level
        previous_state = self._state

        if temperature >= config.max_temp:
            target_state = GuardianState.REDUCING
        elif temperature <= config.low_temp:
            target_state = (
                GuardianState.RECOVERING if self._level > 0 else GuardianState.IDLE
            )
        else:
            target_state = (
                GuardianState.HOLDING if self._level > 0 else GuardianState.IDLE
            )

        if self._may_step(now) and target_state in (
            GuardianState.REDUCING,
            GuardianState.RECOVERING,
        ):
            if target_state is GuardianState.REDUCING:
                self._level = min(
                    MAX_REDUCTION_LEVEL, self._level + config.reduction_step
                )
            else:
                self._level = max(0.0, self._level - config.reduction_step)
            self._last_step = now

        if self._level == 0.0 and target_state is GuardianState.RECOVERING:
            target_state = GuardianState.IDLE

        self._state = target_state
        changed = self._level != previous_level or self._state is not previous_state
        if changed:
            _LOGGER.debug(
                "Wächter: %.1f °C -> Zustand %s, Reduktion %.0f %%",
                temperature,
                self._state,
                self._level,
            )
        return GuardianDecision(self._level, self._state, changed=changed)

    def _may_step(self, now: float) -> bool:
        if self._last_step is None:
            return True
        return now - self._last_step >= self._config.step_interval

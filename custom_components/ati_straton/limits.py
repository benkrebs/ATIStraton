"""Sicherheitslayer für Kanalschreibvorgänge.

Setzt das vierstufige Schutzmodell aus §4.7 der requirements.md um.

Grundannahme: Das Gerät übernimmt per ``color-change`` gesendete Werte
**ungeprüft**. Es gibt keine geräteseitige Begrenzung, jeder Schutz liegt hier.

Das Modul ist bewusst frei von Home-Assistant-Abhängigkeiten, damit es isoliert
getestet werden kann, und arbeitet durchgängig **fail-closed**: lässt sich eine
Grenze nicht eindeutig bestimmen, wird der Schreibvorgang abgelehnt statt
ungeprüft durchgelassen.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

ABSOLUTE_MIN: int = 0
ABSOLUTE_MAX: int = 255

DEFAULT_SAFETY_FACTOR: float = 1.0
MIN_SAFETY_FACTOR: float = 0.1
MAX_SAFETY_FACTOR: float = 1.0


class ChannelLimitError(ValueError):
    """Ein Schreibvorgang verletzt das Sicherheitsmodell und wurde abgelehnt."""


def _known_maxima(spots: Iterable[Mapping[str, Any]]) -> dict[str, list[int]]:
    """Sammelt je Kanalnamen alle *bekannten* ``max``-Werte über alle Spots.

    Einträge ohne ``max`` tragen bewusst nichts bei — sie dürfen die Grenze
    weder anheben noch (siehe ``from_device``) für sich allein aufheben.
    """
    maxima: dict[str, list[int]] = {}
    for spot in spots:
        for channel in spot.get("channels") or ():
            name = channel.get("name")
            raw = channel.get("max")
            if not isinstance(name, str):
                continue
            maxima.setdefault(name, [])
            if isinstance(raw, bool) or not isinstance(raw, int):
                # Fehlendes oder untypisiertes max: Kanalname bleibt bekannt,
                # aber dieser Eintrag liefert keine Schranke (S2).
                continue
            maxima[name].append(raw)
    return maxima


def _preset_maxima(colors: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Höchster Wert, den die geräteeigenen Farbpresets je Kanal verwenden."""
    maxima: dict[str, int] = {}
    for color in colors:
        for value in color.get("values") or ():
            name = value.get("name")
            raw = value.get("value")
            if not isinstance(name, str):
                continue
            if isinstance(raw, bool) or not isinstance(raw, int):
                continue
            maxima[name] = max(maxima.get(name, 0), raw)
    return maxima


@dataclass(frozen=True, slots=True)
class ChannelLimits:
    """Unveränderliche Kanalgrenzen inklusive globalem Sicherheitsfaktor."""

    ceilings: Mapping[str, int] = field(default_factory=dict)
    safety_factor: float = DEFAULT_SAFETY_FACTOR

    def __post_init__(self) -> None:
        if not MIN_SAFETY_FACTOR <= self.safety_factor <= MAX_SAFETY_FACTOR:
            raise ChannelLimitError(
                f"safety_factor {self.safety_factor} liegt außerhalb von "
                f"[{MIN_SAFETY_FACTOR}, {MAX_SAFETY_FACTOR}]"
            )
        for name, ceiling in self.ceilings.items():
            if not ABSOLUTE_MIN <= ceiling <= ABSOLUTE_MAX:
                raise ChannelLimitError(
                    f"Deckel für Kanal {name!r} liegt mit {ceiling} außerhalb von "
                    f"[{ABSOLUTE_MIN}, {ABSOLUTE_MAX}]"
                )

    @classmethod
    def from_device(
        cls,
        spots: Sequence[Mapping[str, Any]],
        colors: Sequence[Mapping[str, Any]],
        safety_factor: float = DEFAULT_SAFETY_FACTOR,
    ) -> ChannelLimits:
        """Leitet die Deckel aus den Gerätedaten ab (Stufen S1 und S2).

        Der Deckel je Kanal ist die konservativere der beiden Domänen:
        das Minimum der bekannten Hardware-``max``-Werte und der höchste Wert,
        den die geräteeigenen Farbpresets für diesen Kanal verwenden.
        """
        hardware = _known_maxima(spots)
        presets = _preset_maxima(colors)

        ceilings: dict[str, int] = {}
        for name, values in hardware.items():
            if not values:
                # Kein einziger bekannter max-Wert für diesen Namen -> fail closed.
                _LOGGER.warning(
                    "Kanal %s besitzt keinen einzigen bekannten max-Wert; "
                    "Kanal wird deaktiviert (ceiling=0)",
                    name,
                )
                ceilings[name] = 0
                continue

            ceiling = min(values)
            if (preset_max := presets.get(name)) is not None:
                ceiling = min(ceiling, preset_max)
            ceilings[name] = max(ABSOLUTE_MIN, min(ceiling, ABSOLUTE_MAX))

        if not ceilings:
            _LOGGER.error(
                "Aus den Gerätedaten ließen sich keine Kanalgrenzen ableiten; "
                "sämtliche Schreibvorgänge werden abgelehnt"
            )

        return cls(ceilings=ceilings, safety_factor=safety_factor)

    def with_safety_factor(self, safety_factor: float) -> ChannelLimits:
        """Kopie mit geändertem Sicherheitsfaktor (Options Flow)."""
        return ChannelLimits(ceilings=dict(self.ceilings), safety_factor=safety_factor)

    @property
    def channels(self) -> tuple[str, ...]:
        """Bekannte Kanalnamen in stabiler Reihenfolge."""
        return tuple(sorted(self.ceilings))

    def effective_ceiling(self, channel: str) -> int:
        """Wirksamer Deckel eines Kanals inklusive Sicherheitsfaktor (S3).

        Unbekannte Kanäle liefern 0 statt einer Ausnahme, damit Aufrufer den
        Zustand abfragen können, ohne Fehlerbehandlung zu benötigen. Der
        eigentliche Schreibpfad lehnt unbekannte Kanäle in :meth:`clamp` ab.
        """
        ceiling = self.ceilings.get(channel)
        if ceiling is None:
            return 0
        return int(ceiling * self.safety_factor)

    def clamp(self, channel: str, value: Any) -> int:
        """Validiert und begrenzt einen Kanalwert. Einziger zulässiger Schreibpfad.

        Raises:
            ChannelLimitError: Bei unbekanntem Kanal, ungültigem Typ, Wert
                außerhalb ``0..255`` oder wirksamem Deckel von 0.
        """
        # S0 - Typ und Bereich. bool ist Subtyp von int und wird ausgeschlossen,
        # damit True nicht klammheimlich als 1 durchrutscht.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ChannelLimitError(
                f"Kanalwert für {channel!r} muss int sein, ist "
                f"{type(value).__name__}: {value!r}"
            )
        if not ABSOLUTE_MIN <= value <= ABSOLUTE_MAX:
            raise ChannelLimitError(
                f"Kanalwert {value} für {channel!r} liegt außerhalb von "
                f"[{ABSOLUTE_MIN}, {ABSOLUTE_MAX}]"
            )
        if channel not in self.ceilings:
            raise ChannelLimitError(
                f"Unbekannter Kanal {channel!r}; bekannt sind {self.channels}"
            )

        # S1..S3 - Deckel anwenden.
        ceiling = self.effective_ceiling(channel)
        if ceiling <= 0:
            raise ChannelLimitError(
                f"Kanal {channel!r} hat einen wirksamen Deckel von {ceiling} und "
                "ist damit gesperrt; Schreibvorgang abgelehnt"
            )
        if value > ceiling:
            _LOGGER.warning(
                "Kanalwert %s für %s überschreitet den Deckel %s und wurde geklemmt",
                value,
                channel,
                ceiling,
            )
            return ceiling
        return value

    def scale_brightness(self, channel: str, brightness: Any) -> int:
        """Bildet HA-``brightness`` (0..255) auf den Deckel des Kanals ab.

        ``brightness = 255`` entspricht dem jeweiligen Kanaldeckel, nicht dem
        Rohwert 255. Die Abbildung ist verlustbehaftet — bei einem Deckel von 15
        bleiben nur 16 unterscheidbare Stufen.
        """
        if isinstance(brightness, bool) or not isinstance(brightness, int):
            raise ChannelLimitError(
                f"brightness muss int sein, ist {type(brightness).__name__}: "
                f"{brightness!r}"
            )
        if not ABSOLUTE_MIN <= brightness <= ABSOLUTE_MAX:
            raise ChannelLimitError(
                f"brightness {brightness} liegt außerhalb von "
                f"[{ABSOLUTE_MIN}, {ABSOLUTE_MAX}]"
            )
        if channel not in self.ceilings:
            raise ChannelLimitError(
                f"Unbekannter Kanal {channel!r}; bekannt sind {self.channels}"
            )

        ceiling = self.effective_ceiling(channel)
        if ceiling <= 0:
            raise ChannelLimitError(
                f"Kanal {channel!r} hat einen wirksamen Deckel von {ceiling} und "
                "ist damit gesperrt; Schreibvorgang abgelehnt"
            )
        # round() statt int(): sonst erreicht brightness=255 den Deckel nie exakt.
        scaled = round(brightness / ABSOLUTE_MAX * ceiling)
        # Defensiv gegen Fließkommadrift - das Ergebnis muss den Deckel halten.
        return max(ABSOLUTE_MIN, min(scaled, ceiling))

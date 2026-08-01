"""Lichtprogramme (Presettings), Gewöhnungsstufen und Farbzusammensetzungen.

Reine Logik ohne Home-Assistant-Abhängigkeiten.

Am Gerät verifiziert (Playwright-Mitschnitt der Originaloberfläche): Ein
Programmwechsel läuft **zweistufig** ab::

    POST /load-presettings   { …Presetting…, intensity, start, end, groups }
    PUT  /api/data           { timelines, spots, colors }

Der erste Aufruf liefert den neuen Tagesverlauf, der zweite schreibt ihn fest.
Zwischen beiden lagen im Mitschnitt rund 0,4 Sekunden.

Jedes Programm bringt genau drei **Gewöhnungsstufen** mit, die im Gerät
``intensities`` heißen:

===== ===================== ============================== ==========
Stufe Bezeichnung           Für                            Vorzugswert
===== ===================== ============================== ==========
1     Eingewöhnungsphase    lichtempfindliche Korallen     30
2     Lichtgewöhnt          lichtgewöhnte Korallen         60
3     Starklichtgewöhnt     stark lichtgewöhnte Korallen   90
===== ===================== ============================== ==========
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

MIN_CHANNEL_VALUE = 0
MAX_CHANNEL_VALUE = 255


class ProgramError(ValueError):
    """Programm oder Farbe konnte nicht verarbeitet werden."""


def translate(translations: Mapping[str, str] | None, key: Any) -> str:
    """Löst einen Übersetzungsschlüssel auf; unbekannte Werte bleiben unverändert."""
    if not isinstance(key, str):
        return ""
    if not translations:
        return key
    return translations.get(key, key)


@dataclass(frozen=True, slots=True)
class AcclimatisationLevel:
    """Eine der drei Gewöhnungsstufen eines Programms."""

    index: int
    title: str
    description: str
    preferred: int
    range_min: float
    range_max: float

    @property
    def label(self) -> str:
        return f"{self.title} ({self.preferred} %)"


@dataclass(frozen=True, slots=True)
class Program:
    """Ein Lichtprogramm des Geräts."""

    id: int
    title: str
    description: str
    group: str
    filename: str
    is_custom: bool
    levels: tuple[AcclimatisationLevel, ...]
    raw: Mapping[str, Any]

    @property
    def label(self) -> str:
        """Eindeutiger, menschenlesbarer Name für die Auswahlliste."""
        return f"{self.group} · {self.title}" if self.group else self.title


def parse_levels(
    presetting: Mapping[str, Any], translations: Mapping[str, str] | None = None
) -> tuple[AcclimatisationLevel, ...]:
    levels: list[AcclimatisationLevel] = []
    for index, entry in enumerate(presetting.get("intensities") or ()):
        if not isinstance(entry, Mapping):
            continue
        bounds = entry.get("range") or {}
        levels.append(
            AcclimatisationLevel(
                index=index,
                title=translate(translations, entry.get("title")),
                description=translate(translations, entry.get("description")),
                preferred=int(entry.get("preferred", 0)),
                range_min=float(bounds.get("min", 0)),
                range_max=float(bounds.get("max", 100)),
            )
        )
    return tuple(levels)


def parse_programs(
    presettings: Sequence[Mapping[str, Any]],
    translations: Mapping[str, str] | None = None,
) -> list[Program]:
    """Wandelt ``/api/presettings`` in auswertbare Programme um."""
    programs: list[Program] = []
    for entry in presettings:
        if not isinstance(entry, Mapping) or "_id" not in entry:
            continue
        if entry.get("disabled"):
            continue
        is_custom = bool(entry.get("isCustom"))
        group_key = entry.get("group") or (
            "PRESETTING_GROUP_CUSTOM" if is_custom else None
        )
        programs.append(
            Program(
                id=int(entry["_id"]),
                title=translate(translations, entry.get("title")),
                description=translate(translations, entry.get("description")),
                group=translate(translations, group_key) if group_key else "",
                filename=str(entry.get("filename") or ""),
                is_custom=is_custom,
                levels=parse_levels(entry, translations),
                raw=entry,
            )
        )
    programs.sort(key=lambda p: (p.group, p.title))
    return programs


DEFAULT_START = 32400
"""09:00 — Rückfallwert, entspricht dem Vorgabewert der Geräteoberfläche."""

DEFAULT_END = 81000
"""22:30 — Rückfallwert."""


def derive_timerange(
    program: Program, timelines: Sequence[Mapping[str, Any]] | None = None
) -> tuple[int, int]:
    """Ermittelt den Beleuchtungszeitraum für einen Programmwechsel.

    Der Zeitbereich gehört **nicht** fest zum Programm: Im Mitschnitt der
    Originaloberfläche wich er vom ``timerange`` des Presettings ab (Programm B
    sendete 32400 statt der hinterlegten 37800), und eigene Presettings bringen
    gar keinen mit.

    Reihenfolge: bestehender Tagesverlauf (damit das gewohnte Lichtfenster
    erhalten bleibt), sonst der Vorgabewert des Programms, sonst 09:00–22:30.
    """
    if timelines:
        lit = [
            float(node["time"])
            for timeline in timelines
            for node in timeline.get("nodes") or ()
            if isinstance(node.get("time"), (int, float))
            and isinstance(node.get("value"), (int, float))
            and float(node["value"]) > 0
        ]
        if lit:
            return int(min(lit)), int(max(lit))

    timerange = program.raw.get("timerange") or {}
    start, end = timerange.get("start"), timerange.get("end")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return int(start), int(end)

    return DEFAULT_START, DEFAULT_END


def build_load_payload(
    program: Program,
    level_index: int,
    group_ids: Sequence[int],
    intensity: int | None = None,
    timerange: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Baut die Nutzlast für ``POST /load-presettings``.

    Die Struktur folgt exakt dem aufgezeichneten Aufruf der Originaloberfläche:
    das vollständige Presetting, ergänzt um ``intensity``, ``start``, ``end`` und
    ``groups``. In ``intensities`` wird genau die gewählte Stufe als
    ``highlighted`` markiert.
    """
    if not program.levels:
        raise ProgramError(f"Programm {program.title!r} hat keine Gewöhnungsstufen")
    if not 0 <= level_index < len(program.levels):
        raise ProgramError(
            f"Gewöhnungsstufe {level_index} existiert nicht; "
            f"verfügbar sind 0..{len(program.levels) - 1}"
        )
    if not group_ids:
        raise ProgramError("Keine Ziel-Timeline angegeben")

    level = program.levels[level_index]
    payload = copy.deepcopy(dict(program.raw))

    for index, entry in enumerate(payload.get("intensities") or ()):
        entry["highlighted"] = index == level_index

    start, end = timerange if timerange else derive_timerange(program)
    if start >= end:
        raise ProgramError(f"Ungültiger Zeitbereich: start={start}, end={end}")

    # Eigene Presettings bringen keinen timerange mit; die Oberfläche ergänzt ihn
    # ebenfalls, deshalb wird er hier gesetzt statt vorausgesetzt.
    payload["timerange"] = {"start": start, "end": end}
    payload["intensity"] = int(intensity if intensity is not None else level.preferred)
    payload["start"] = start
    payload["end"] = end
    payload["groups"] = list(group_ids)
    return payload


# --------------------------------------------------------------------- Farben


@dataclass(frozen=True, slots=True)
class ColorChannel:
    """Ein Kanalanteil innerhalb einer Farbzusammensetzung."""

    id: int
    name: str
    label: str
    value: int


@dataclass(frozen=True, slots=True)
class ColorPreset:
    """Eine Farbe (Spektrum) des Geräts."""

    id: int
    name: str
    disabled: bool
    channels: tuple[ColorChannel, ...]

    @property
    def composition(self) -> dict[str, int]:
        return {channel.name: channel.value for channel in self.channels}


def parse_colors(
    colors: Sequence[Mapping[str, Any]],
    translations: Mapping[str, str] | None = None,
) -> list[ColorPreset]:
    """Wandelt ``/api/colors`` in auswertbare Farben um."""
    result: list[ColorPreset] = []
    for entry in colors:
        if not isinstance(entry, Mapping) or "_id" not in entry:
            continue
        channels = tuple(
            ColorChannel(
                id=int(value.get("id", -1)),
                name=str(value.get("name", "")),
                label=translate(translations, value.get("label")),
                value=int(value.get("value", 0)),
            )
            for value in entry.get("values") or ()
            if isinstance(value, Mapping)
        )
        result.append(
            ColorPreset(
                id=int(entry["_id"]),
                name=str(entry.get("name") or ""),
                disabled=bool(entry.get("disabled")),
                channels=channels,
            )
        )
    return result


def colors_in_schedule(
    timelines: Sequence[Mapping[str, Any]],
    translations: Mapping[str, str] | None = None,
) -> list[ColorPreset]:
    """Farben, die der aktuelle Tagesverlauf tatsächlich verwendet.

    Jeder Kurvenknoten trägt sein vollständiges Farbpreset. Ein Programm nutzt
    typischerweise nur zwei bis drei davon — etwa ein kühleres Spektrum für den
    Tag und ein wärmeres für die Dämmerung.

    Die Reihenfolge folgt dem ersten Auftreten im Tagesverlauf, nicht der
    Reihenfolge in ``/api/colors``.
    """
    seen: dict[int, Mapping[str, Any]] = {}
    for timeline in timelines:
        for node in timeline.get("nodes") or ():
            color = node.get("color")
            if isinstance(color, Mapping) and "_id" in color:
                seen.setdefault(int(color["_id"]), color)
    return parse_colors(list(seen.values()), translations)


def schedule_overview(
    timelines: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Der Tagesverlauf als flache Liste: Uhrzeit, Intensität, Farbe.

    ``time`` kommt vom Gerät in Sekunden seit Mitternacht und wird zusätzlich
    als ``HH:MM`` ausgegeben, damit es ohne Umrechnung lesbar ist.
    """
    overview: list[dict[str, Any]] = []
    for timeline in timelines:
        for node in timeline.get("nodes") or ():
            seconds = node.get("time")
            if not isinstance(seconds, (int, float)):
                continue
            color = node.get("color")
            overview.append(
                {
                    "time": f"{int(seconds) // 3600:02d}:{int(seconds) % 3600 // 60:02d}",
                    "seconds": int(seconds),
                    "intensity": round(float(node.get("value", 0)), 2),
                    "color": color.get("name") if isinstance(color, Mapping) else None,
                }
            )
    return sorted(overview, key=lambda entry: entry["seconds"])


def with_updated_color(
    colors: Sequence[Mapping[str, Any]],
    color_id: int,
    values: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Erzeugt eine Kopie von ``colors`` mit geänderter Zusammensetzung.

    Der Wertebereich ist ``0..255``. Er stammt aus der Preset-Domäne des Geräts —
    dessen eigene Farben nutzen bis 255 — und ist **nicht** mit den
    Hardwaregrenzen aus :mod:`.limits` zu verwechseln, die in einer anderen Skala
    liegen.

    Raises:
        ProgramError: Bei unbekannter Farbe, unbekanntem Kanal oder Werten
            außerhalb des zulässigen Bereichs.
    """
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProgramError(
                f"Wert für Kanal {name!r} muss ganzzahlig sein, ist "
                f"{type(value).__name__}"
            )
        if not MIN_CHANNEL_VALUE <= value <= MAX_CHANNEL_VALUE:
            raise ProgramError(
                f"Wert {value} für Kanal {name!r} liegt außerhalb von "
                f"[{MIN_CHANNEL_VALUE}, {MAX_CHANNEL_VALUE}]"
            )

    result = copy.deepcopy([dict(color) for color in colors])
    target = next((color for color in result if color.get("_id") == color_id), None)
    if target is None:
        raise ProgramError(f"Farbe mit _id={color_id} nicht gefunden")

    known = {entry.get("name") for entry in target.get("values") or ()}
    if unknown := set(values) - known:
        raise ProgramError(
            f"Unbekannte Kanäle {sorted(unknown)}; verfügbar sind {sorted(known)}"
        )

    for entry in target.get("values") or ():
        if (name := entry.get("name")) in values:
            entry["value"] = values[name]
    return result

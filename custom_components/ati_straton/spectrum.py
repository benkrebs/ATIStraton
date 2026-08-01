"""Näherungsweise Umrechnung einer Kanalmischung in eine Bildschirmfarbe.

Dient ausschließlich der Darstellung: ein Farbfeld neben einem Regler ist
schneller erfasst als sechs Zahlen. Für die Steuerung wird nichts davon
verwendet.

**Was das nicht ist:** keine kolorimetrisch korrekte Umrechnung. Die
Kanalfarben sind grobe sRGB-Näherungen der jeweiligen Peak-Wellenlänge, die
spektrale Empfindlichkeit des Auges bleibt unberücksichtigt, und Kanäle
außerhalb des sichtbaren Bereichs — allen voran UV — lassen sich ohnehin nicht
sinnvoll darstellen.

Riffbeleuchtung ist durchweg blaulastig. Die Farbfelder unterscheiden daher vor
allem „kräftig und kalt" von „hell und weißlich"; für feine Abstufungen bleiben
die Zahlen maßgeblich.
"""

from __future__ import annotations

from collections.abc import Mapping

Rgb = tuple[int, int, int]

CHANNEL_COLORS: dict[str, Rgb] = {
    "V": (138, 43, 226),
    "UV": (110, 20, 200),
    "RB": (0, 60, 255),
    "RB-V": (60, 50, 240),
    "B": (0, 130, 255),
    "LC": (0, 220, 200),
    "W": (255, 250, 235),
    "WW": (255, 220, 170),
    "CW": (225, 240, 255),
    "HW": (255, 245, 220),
    "T5": (245, 245, 245),
    "R": (255, 30, 0),
}
"""Näherungsfarbe je Kanalcode. Unbekannte Kanäle bleiben unberücksichtigt."""

NEUTRAL = "#808080"
"""Rückfallwert, wenn sich keine Farbe bestimmen lässt."""

CHANNEL_NAMES: dict[str, dict[str, str]] = {
    "V": {"de": "Violett", "en": "Violet"},
    "UV": {"de": "Ultraviolett", "en": "Ultraviolet"},
    "RB": {"de": "Royalblau", "en": "Royal blue"},
    "RB-V": {"de": "Royalblau/Violett", "en": "Royal blue/violet"},
    "B": {"de": "Blau", "en": "Blue"},
    "LC": {"de": "Cyan", "en": "Cyan"},
    "W": {"de": "Weiß", "en": "White"},
    "WW": {"de": "Warmweiß", "en": "Warm white"},
    "CW": {"de": "Kaltweiß", "en": "Cool white"},
    # HW hinterlegt die Firmware nirgends als Klartext; der Code bleibt stehen,
    # statt eine Bedeutung zu erfinden.
    "HW": {"de": "Weiß HW", "en": "White HW"},
    "T5": {"de": "T5-Leuchtstoff", "en": "T5 fluorescent"},
    "R": {"de": "Rot", "en": "Red"},
}
"""Ausgeschriebene Kanalnamen. Die Firmware kennt nur die Kurzcodes."""


CHANNEL_ORDER: tuple[str, ...] = (
    "V",
    "B",
    "RB",
    "LC",
    "W",
    "R",
    "UV",
    "RB-V",
    "WW",
    "CW",
    "HW",
    "T5",
)
"""Reihenfolge, in der die Kanalregler erscheinen sollen.

Home Assistant sortiert Entitäten auf der Geräteseite alphabetisch nach ihrem
Namen und bietet dafür keine Einstellung. Die Reihenfolge lässt sich deshalb nur
über den Namen steuern — daher die vorangestellte Ziffer in
:func:`channel_name`.

Die Reihenfolge weicht bewusst leicht von der des Geräts ab, das ``RB`` vor
``B`` einsortiert.
"""


def channel_position(channel: str) -> int:
    """Platz eines Kanals in der Anzeige, beginnend bei 1."""
    try:
        return CHANNEL_ORDER.index(channel) + 1
    except ValueError:
        return len(CHANNEL_ORDER) + 1


def channel_name(channel: str, language: str = "en", numbered: bool = True) -> str:
    """Ausgeschriebener Name eines Kanals, mit dem Code in Klammern.

    Der Code bleibt sichtbar, weil ``ati_straton.set_color`` ihn als Schlüssel
    erwartet — und weil er bei ``LC`` von der Beschriftung des Geräts abweicht,
    die dort schlicht ``C`` lautet.

    Args:
        numbered: Stellt die Position voran, damit die alphabetische Sortierung
            von Home Assistant die gewünschte Reihenfolge ergibt. Ohne das
            stünde Blau vor Violett.
    """
    lang = "de" if language.startswith("de") else "en"
    written = CHANNEL_NAMES.get(channel, {}).get(lang)
    if not written:
        return channel
    label = f"{written} ({channel})"
    prefix = "Kanal" if lang == "de" else "Channel"
    return f"{prefix} {channel_position(channel)} {label}" if numbered else label


def channel_hex(channel: str) -> str:
    """Feste Anzeigefarbe eines Kanals, etwa für die Beschriftung eines Reglers."""
    rgb = CHANNEL_COLORS.get(channel)
    return NEUTRAL if rgb is None else "#{:02X}{:02X}{:02X}".format(*rgb)


def mix_rgb(composition: Mapping[str, int]) -> Rgb | None:
    """Mischt eine Kanalzusammensetzung zu einer Näherungsfarbe.

    Gewichtet die Kanalfarben mit ihren Anteilen und normiert anschließend auf
    Vollaussteuerung — sonst erschiene jede Mischung dunkelgrau, weil die Summe
    der Anteile die Helligkeit dominiert.

    Returns:
        ``None``, wenn kein bekannter Kanal einen Anteil größer null hat.
    """
    weights = {
        name: value
        for name, value in composition.items()
        if name in CHANNEL_COLORS and isinstance(value, (int, float)) and value > 0
    }
    total = sum(weights.values())
    if not total:
        return None

    channels = [0.0, 0.0, 0.0]
    for name, value in weights.items():
        for index, component in enumerate(CHANNEL_COLORS[name]):
            channels[index] += component * value
    channels = [component / total for component in channels]

    peak = max(channels)
    if peak > 0:
        scale = 255.0 / peak
        channels = [component * scale for component in channels]
    return tuple(int(min(255, max(0, round(c)))) for c in channels)  # type: ignore[return-value]


def mix_hex(composition: Mapping[str, int]) -> str | None:
    """Wie :func:`mix_rgb`, aber als ``#RRGGBB``."""
    rgb = mix_rgb(composition)
    return None if rgb is None else "#{:02X}{:02X}{:02X}".format(*rgb)

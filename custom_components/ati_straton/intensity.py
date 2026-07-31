"""Globale Intensitätssteuerung des Tagesverlaufs.

Am Gerät verifiziert: Die Oberfläche skaliert beim Verstellen des
Intensitätsreglers **alle** Kurvenknoten relativ zu ihrem unveränderlichen
Originalwert ``valueOrg`` und speichert anschließend mit ``PUT /api/data``::

    node.value = node.valueOrg × n / maxValueOrg      # n = Regler 0…100

``valueOrg`` wird dabei nie verändert und dient als stabiler Anker — die
Operation ist deshalb verlustfrei umkehrbar.

Messung am Gerät (2026-07-31): Spitzenintensität 60 → 27 senkte die Stromaufnahme
von 516 auf 267 ADC, wirksam **innerhalb von drei Sekunden**. Rücksetzung stellte
516 ADC und alle 16 Knotenwerte exakt wieder her.

Dies ist der **einzige nachweislich funktionierende Steuerpfad**. Der
Socket-Pfad (``color-preview``/``color-change``) bleibt am Gerät wirkungslos,
auch mit korrektem Engine.IO-3-Protokoll.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Sequence
from typing import Any

_LOGGER = logging.getLogger(__name__)

MIN_INTENSITY = 0.0
MAX_INTENSITY = 100.0


class IntensityError(ValueError):
    """Die Intensität konnte nicht bestimmt oder angewandt werden."""


def _nodes(timelines: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [node for timeline in timelines for node in timeline.get("nodes") or ()]


def max_value_org(timelines: Sequence[dict[str, Any]]) -> float:
    """Höchster ``valueOrg`` über alle Knoten — der Bezugswert der Skalierung."""
    values = [
        float(node["valueOrg"])
        for node in _nodes(timelines)
        if isinstance(node.get("valueOrg"), (int, float))
    ]
    return max(values) if values else 0.0


def current_intensity(timelines: Sequence[dict[str, Any]]) -> float:
    """Aktuelle Reglerstellung, abgeleitet aus dem höchsten ``value``.

    Entspricht ``getMaxIntensity()`` der Geräteoberfläche.
    """
    values = [
        float(node["value"])
        for node in _nodes(timelines)
        if isinstance(node.get("value"), (int, float))
    ]
    return max(values) if values else 0.0


def scaled_timelines(
    timelines: Sequence[dict[str, Any]], intensity: float
) -> list[dict[str, Any]]:
    """Erzeugt eine Kopie der Timelines mit auf ``intensity`` skalierten Knoten.

    Die Eingabe bleibt unverändert. ``valueOrg`` wird nicht angetastet.

    Raises:
        IntensityError: Bei ungültiger Intensität oder fehlendem Bezugswert.
    """
    if isinstance(intensity, bool) or not isinstance(intensity, (int, float)):
        raise IntensityError(
            f"Intensität muss eine Zahl sein, ist {type(intensity).__name__}"
        )
    if not MIN_INTENSITY <= intensity <= MAX_INTENSITY:
        raise IntensityError(
            f"Intensität {intensity} liegt außerhalb von "
            f"[{MIN_INTENSITY}, {MAX_INTENSITY}]"
        )

    reference = max_value_org(timelines)
    if reference <= 0:
        raise IntensityError(
            "Kein verwertbarer valueOrg in den Timelines; Skalierung abgelehnt"
        )

    result = copy.deepcopy(list(timelines))
    for timeline in result:
        for node in timeline.get("nodes") or ():
            value_org = node.get("valueOrg")
            if not isinstance(value_org, (int, float)):
                # Ohne Anker nicht skalierbar: Knoten unverändert lassen, statt
                # zu raten.
                continue
            node["value"] = (
                round(float(value_org) * intensity / reference, 2)
                if intensity > 0
                else 0.0
            )
    return result


def rescaled_by_factor(
    timelines: Sequence[dict[str, Any]], factor: float
) -> list[dict[str, Any]]:
    """Skaliert die **aktuellen** Werte um ``factor``, ohne die Regler-Formel.

    Wird vom Temperaturwächter verwendet: Anders als :func:`scaled_timelines`
    normalisiert diese Variante keine Knoten, die von der Formel abweichen. Am
    Testgerät existierten solche Knoten tatsächlich (``value == valueOrg``,
    während alle übrigen bei Verhältnis 0,8125 lagen). Ein exakter Schnappschuss
    lässt sich damit verlustfrei zurückschreiben.
    """
    if not 0.0 <= factor <= 1.0:
        raise IntensityError(f"Faktor {factor} liegt außerhalb von [0.0, 1.0]")

    result = copy.deepcopy(list(timelines))
    for timeline in result:
        for node in timeline.get("nodes") or ():
            value = node.get("value")
            if isinstance(value, (int, float)):
                # Drei Nachkommastellen: Das Gerät speichert selbst mit dieser
                # Genauigkeit (z. B. 63.375). Mit zwei Stellen wäre schon
                # factor=1.0 kein No-op mehr und jeder Aufruf würde Präzision
                # verlieren.
                node["value"] = round(float(value) * factor, 3)
    return result


def node_values(timelines: Sequence[dict[str, Any]]) -> list[float]:
    """Flache Liste aller Knotenwerte — für Vergleiche und Prüfungen."""
    return [
        float(node["value"])
        for node in _nodes(timelines)
        if isinstance(node.get("value"), (int, float))
    ]

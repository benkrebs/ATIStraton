"""Ausliefern und Anmelden der Dashboard-Karte.

Die Karte liegt bewusst im Paket der Integration statt in einem eigenen
HACS-Repository: Sie liest Attribute, die nur diese Integration liefert, und
muss mit ihr im Gleichschritt versioniert werden. Eine getrennt installierte
Karte könnte auf einen Stand treffen, dessen Attribute sie nicht kennt.

Home Assistant lädt die Datei auf jeder Seite des Frontends als Modul nach. Ein
manueller Eintrag unter *Dashboard-Ressourcen* ist dadurch nicht nötig.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

CARD_FILE = Path(__file__).parent / "card" / "ati-straton-card.js"
CARD_URL = "/ati_straton/ati-straton-card.js"
_REGISTERED = "ati_straton_card_registered"


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


async def async_register_card(hass: HomeAssistant) -> None:
    """Meldet die Karte einmalig an, auch bei mehreren Geräten.

    Die Abfragekennung ist ein Prüfwert des Dateiinhalts. Ändert sich die Karte
    mit einem Update, lädt der Browser sie neu, statt eine alte Fassung aus dem
    Cache zu verwenden.
    """
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True
    fingerprint = await hass.async_add_executor_job(_fingerprint, CARD_FILE)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=True)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={fingerprint}")

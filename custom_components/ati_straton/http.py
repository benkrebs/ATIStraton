"""Ausliefern kleiner Farbpunkte für die Kanalregler.

Home Assistant kann Entitäts-Icons nicht einfärben. Ein Entität darf aber ein
``entity_picture`` führen, das die Oberfläche anstelle des Icons anzeigt. Diese
Ansicht liefert dafür einen gefüllten Kreis in der Kanalfarbe.

Bewusst als eigene Route statt als ``data:``-URI: Ob das Frontend Daten-URIs für
Bilder zulässt, hängt an dessen Content-Security-Policy und lässt sich nicht
zuverlässig voraussetzen. Eine Route derselben Herkunft funktioniert immer.
"""

from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .spectrum import CHANNEL_COLORS, channel_hex

URL_BASE = "/api/ati_straton/channel"
_REGISTERED = "ati_straton_channel_view"

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    '<circle cx="20" cy="20" r="20" fill="{fill}"/>'
    "</svg>"
)


def async_register_view(hass) -> None:
    """Registriert die Ansicht einmalig, auch bei mehreren Geräten."""
    if hass.data.setdefault(_REGISTERED, False):
        return
    hass.http.register_view(StratonChannelIconView)
    hass.data[_REGISTERED] = True


def channel_picture_url(channel: str) -> str:
    """Adresse des Farbpunkts eines Kanals."""
    return f"{URL_BASE}/{channel}"


class StratonChannelIconView(HomeAssistantView):
    """Liefert den Farbpunkt zu einem Kanalcode."""

    url = URL_BASE + "/{channel}"
    name = "api:ati_straton:channel"
    # Der Punkt enthält keinerlei schützenswerte Angabe, und Bilder werden vom
    # Browser ohne Authentifizierungskopf geladen.
    requires_auth = False

    async def get(self, request: web.Request, channel: str) -> web.Response:
        # Nur bekannte Codes bedienen. Damit gelangt niemals eine fremde
        # Zeichenkette in das ausgelieferte SVG.
        if channel not in CHANNEL_COLORS:
            return web.Response(status=404)
        return web.Response(
            body=_SVG.format(fill=channel_hex(channel)).encode(),
            content_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

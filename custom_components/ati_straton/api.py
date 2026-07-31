"""HTTP-Client für das ATI Straton Gerät.

Bewusst frei von Home-Assistant-Abhängigkeiten, damit der Client gegen einen
Mock-Server getestet werden kann. Übersetzung in HA-Exceptions erfolgt im
Coordinator.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import TracebackType
from typing import Any, Self

import aiohttp
from yarl import URL

from .const import FORBIDDEN_ENDPOINTS

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15)


class StratonError(Exception):
    """Basisfehler der Geräteanbindung."""


class StratonAuthError(StratonError):
    """Anmeldung fehlgeschlagen oder Session nicht wiederherstellbar."""


class StratonConnectionError(StratonError):
    """Gerät nicht erreichbar oder Antwort unbrauchbar."""


class StratonApiClient:
    """Sessionbasierter Zugriff auf die ``/api/*``-Endpunkte des Geräts."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base = URL(host if "://" in host else f"http://{host}")
        self._username = username
        self._password = password
        self._login_lock = asyncio.Lock()
        self._logged_in = False
        self._owns_session = session is None
        # unsafe=True ist zwingend: aiohttp verwirft Cookies von IP-Adress-Hosts
        # sonst stillschweigend, und das Gerät wird typischerweise über seine IP
        # angesprochen. Ohne dieses Flag bliebe connect.sid wirkungslos.
        self._session = session or aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=DEFAULT_TIMEOUT,
        )

    @property
    def base_url(self) -> str:
        return str(self._base)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.async_close()

    async def async_close(self) -> None:
        if self._owns_session and not self._session.closed:
            await self._session.close()

    @property
    def cookies(self) -> dict[str, str]:
        """Session-Cookies, damit der Socket-Client dieselbe Session nutzen kann."""
        return {
            cookie.key: cookie.value
            for cookie in self._session.cookie_jar
            if cookie.key == "connect.sid"
        }

    async def async_login(self) -> None:
        """Meldet sich per Form-POST an und hinterlegt ``connect.sid``.

        Das Gerät nutzt ein natives HTML-Formular, kein JSON. Erfolg ist an einem
        302 auf ``/`` erkennbar, Misserfolg an einem 302 zurück auf ``/login``.
        """
        async with self._login_lock:
            self._logged_in = False
            try:
                async with self._session.post(
                    self._base / "login",
                    data={"username": self._username, "password": self._password},
                    allow_redirects=False,
                ) as response:
                    location = response.headers.get("Location", "")
                    if response.status != 302 or location.rstrip("/").endswith("login"):
                        raise StratonAuthError(
                            "Anmeldung abgelehnt (Status "
                            f"{response.status}, Location {location!r})"
                        )
            except aiohttp.ClientError as err:
                raise StratonConnectionError(
                    f"Anmeldung fehlgeschlagen: {err}"
                ) from err
            except TimeoutError as err:
                raise StratonConnectionError(
                    "Zeitüberschreitung bei der Anmeldung"
                ) from err

            if not self.cookies:
                raise StratonAuthError("Gerät hat kein Session-Cookie gesetzt")
            self._logged_in = True
            _LOGGER.debug("Anmeldung am Gerät %s erfolgreich", self._base)

    async def async_get(self, endpoint: str) -> Any:
        """Liest einen ``/api``-Endpunkt, mit einmaligem Re-Login bei 401."""
        return await self._request("GET", endpoint)

    async def async_post(self, endpoint: str, payload: Any) -> Any:
        return await self._request("POST", endpoint, payload)

    async def async_post_root(self, path: str, payload: Any) -> Any:
        """POST auf einen Pfad **außerhalb** von ``/api/``.

        Das Gerät legt genau einen schreibenden Endpunkt in die Wurzel:
        ``/load-presettings``. Ein Aufruf unter ``/api/`` quittiert es mit 404.
        """
        return await self._request("POST", path, payload, root=True)

    async def async_get_translations(self, language: str = "de_DE") -> dict[str, str]:
        """Lädt die Sprachdatei des Geräts.

        Liegt außerhalb von ``/api/`` und löst Schlüssel wie
        ``PRESETTING_TITLE_8_1`` in lesbare Bezeichnungen auf. Ein Fehlschlag ist
        unkritisch — ohne Übersetzung bleiben die Rohschlüssel stehen.
        """
        try:
            async with self._session.get(
                self._base / "lang" / f"lang-{language}.json", allow_redirects=False
            ) as response:
                if response.status != 200:
                    return {}
                parsed = json.loads(await response.text())
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Sprachdatei %s nicht ladbar: %s", language, err)
            return {}
        return (
            {k: v for k, v in parsed.items() if isinstance(v, str)}
            if isinstance(parsed, dict)
            else {}
        )

    async def async_put(self, endpoint: str, payload: Any) -> Any:
        return await self._request("PUT", endpoint, payload)

    async def _request(
        self,
        method: str,
        endpoint: str,
        payload: Any = None,
        *,
        root: bool = False,
        _retry: bool = True,
    ) -> Any:
        name = endpoint.strip("/")
        if name in FORBIDDEN_ENDPOINTS:
            # /api/user und /api/network liefern Geheimnisse im Klartext
            # (Sicherheitsbefunde S-01/S-04). Hart gesperrt, damit auch kein
            # künftiger Codepfad sie versehentlich abruft.
            raise StratonError(
                f"Endpunkt {name!r} ist gesperrt: liefert Geheimnisse im Klartext"
            )

        if not self._logged_in:
            await self.async_login()

        url = self._base / name if root else self._base / "api" / name
        try:
            async with self._session.request(
                method,
                url,
                json=payload if payload is not None else None,
                allow_redirects=False,
            ) as response:
                if self._is_session_expired(response):
                    self._logged_in = False
                    if not _retry:
                        raise StratonAuthError(
                            f"Session für {name!r} auch nach Re-Login ungültig"
                        )
                    _LOGGER.debug("Session abgelaufen bei %s, melde neu an", name)
                    await self.async_login()
                    return await self._request(
                        method, name, payload, root=root, _retry=False
                    )

                if response.status >= 400:
                    raise StratonConnectionError(
                        f"{method} {name} scheiterte mit Status {response.status}"
                    )
                return await self._parse(response, name)
        except aiohttp.ClientError as err:
            raise StratonConnectionError(
                f"{method} {name} fehlgeschlagen: {err}"
            ) from err
        except TimeoutError as err:
            raise StratonConnectionError(
                f"Zeitüberschreitung bei {method} {name}"
            ) from err

    @staticmethod
    def _is_session_expired(response: aiohttp.ClientResponse) -> bool:
        if response.status == 401:
            return True
        location = response.headers.get("Location", "")
        return response.status in (301, 302, 303, 307, 308) and "login" in location

    @staticmethod
    async def _parse(response: aiohttp.ClientResponse, name: str) -> Any:
        text = await response.text()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            # /api/time liefert einen nackten ISO-String mit text/plain statt
            # JSON. Rohtext zurückgeben statt den Aufruf scheitern zu lassen.
            _LOGGER.debug("Antwort von %s ist kein JSON, gebe Rohtext zurück", name)
            return text

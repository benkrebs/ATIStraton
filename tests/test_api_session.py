"""Tests der Sessionbehandlung des HTTP-Clients.

Hintergrund: ``aiohttp`` verwirft Cookies von IP-Adress-Hosts stillschweigend,
solange der Jar nicht ``unsafe=True`` gesetzt hat. Die Anmeldung am Gerät kommt
dann zwar durch, das Session-Cookie landet aber nie im Jar — jeder Folgeaufruf
scheitert.

Der Mock-Server läuft auf ``127.0.0.1`` und reproduziert damit genau die
Bedingung, unter der Home Assistant die Integration betreibt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton.api import (
    StratonApiClient,
    StratonAuthError,
    StratonConnectionError,
    create_cookie_jar,
)

SESSION_COOKIE = "connect.sid"


async def _login(request: web.Request) -> web.Response:
    form = await request.post()
    if form.get("password") != "richtig":
        return web.Response(status=302, headers={"Location": "/login"})
    response = web.Response(status=302, headers={"Location": "/"})
    response.set_cookie(SESSION_COOKIE, "s%3Atestsession", httponly=True, path="/")
    return response


async def _state(request: web.Request) -> web.Response:
    if SESSION_COOKIE not in request.cookies:
        return web.Response(status=401, text="Unauthorized")
    return web.json_response({"initialized": True})


@pytest.fixture(name="server")
async def server_fixture() -> str:
    app = web.Application()
    app.router.add_post("/login", _login)
    app.router.add_get("/api/state", _state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.mark.asyncio
class TestCookieJar:
    async def test_own_session_keeps_the_cookie(self, server: str) -> None:
        """Ohne übergebene Session baut der Client den Jar selbst korrekt."""
        async with StratonApiClient(server, "user", "richtig") as client:
            await client.async_login()
            assert client.cookies
            assert await client.async_get("state") == {"initialized": True}

    async def test_helper_jar_keeps_the_cookie(self, server: str) -> None:
        """Der Weg, den Home Assistant nimmt: fremde Session plus Helfer-Jar."""
        session = aiohttp.ClientSession(cookie_jar=create_cookie_jar())
        try:
            client = StratonApiClient(server, "user", "richtig", session=session)
            await client.async_login()
            assert await client.async_get("state") == {"initialized": True}
        finally:
            await session.close()

    async def test_default_jar_is_rejected_with_a_clear_message(
        self, server: str
    ) -> None:
        """Regression: genau dieser Fall führte in HA zu einem irreführenden
        ``invalid_auth``, obwohl die Zugangsdaten stimmten."""
        session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar())
        try:
            client = StratonApiClient(server, "user", "richtig", session=session)
            with pytest.raises(StratonConnectionError, match="unsafe=True"):
                await client.async_login()
        finally:
            await session.close()

    async def test_wrong_password_stays_an_auth_error(self, server: str) -> None:
        """Falsche Zugangsdaten dürfen nicht mit dem Cookie-Problem verwechselt
        werden — nur hier ist ``invalid_auth`` die richtige Meldung."""
        async with StratonApiClient(server, "user", "falsch") as client:
            with pytest.raises(StratonAuthError, match="abgelehnt"):
                await client.async_login()

"""Tests der Verbindungsüberwachung des Push-Kanals.

Hintergrund: Die Temperaturtelemetrie kommt ausschließlich über den Socket.
Reißt er ab, ohne dass es jemand bemerkt, bleiben alle Messwerte auf ihrem
letzten Stand stehen — die Integration wirkt dann bis zu einem Neuladen
funktionsfähig, zeigt aber Werte von vor Stunden.

Geprüft wird deshalb beides: dass ein Abriss überhaupt gemeldet wird, und dass
eine halb offene Verbindung — für ``aiohttp`` weiter „offen“, aber ohne jeden
Frame — vom Wächter erkannt und geschlossen wird.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton import eio3
from custom_components.ati_straton.coordinator import SpotReading, StratonData
from custom_components.ati_straton.eio3 import SocketIO2Client

HANDSHAKE = json.dumps(
    {
        "sid": "testsid",
        "upgrades": [],
        "pingInterval": 25000,
        "pingTimeout": 60000,
    }
)

SPOTS_EVENT = '42["temperature-spots",[{"externalId":"dev:1","temperature":25.9}]]'


def _make_app(*, drop_after_event: bool) -> web.Application:
    """Socket.IO-2-Server, der wahlweise abbricht oder stumm offen bleibt."""

    async def handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(f"0{HANDSHAKE}")
        await ws.send_str("40")
        await ws.send_str(SPOTS_EVENT)
        if drop_after_event:
            await ws.close()
            return ws
        # Stumm bleiben: kein Pong, keine Telemetrie. Genau die Lage, in der
        # die Messwerte früher für immer eingefroren wären.
        async for _ in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_get("/socket.io/", handler)
    return app


@pytest.fixture(name="server")
async def server_fixture(request: pytest.FixtureRequest) -> str:
    drop = getattr(request, "param", False)
    runner = web.AppRunner(_make_app(drop_after_event=drop))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


@pytest.mark.parametrize("server", [True], indirect=True)
async def test_abriss_wird_gemeldet(server: str) -> None:
    """Schließt das Gerät die Verbindung, wartet niemand mehr vergeblich."""
    received: list[object] = []
    client = SocketIO2Client(server)
    client.on("temperature-spots", received.append)

    await client.async_connect("connect.sid=egal")
    try:
        await asyncio.wait_for(client.async_wait_closed(), 5)
        assert received, "Telemetrie muss vor dem Abriss angekommen sein"
        assert client.connected is False
    finally:
        await client.async_disconnect()


@pytest.mark.parametrize("server", [False], indirect=True)
async def test_waechter_schliesst_stumme_verbindung(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine offene, aber stumme Verbindung gilt nach Ablauf der Frist als tot."""
    monkeypatch.setattr(eio3, "WATCHDOG_TICK", 0.05)
    client = SocketIO2Client(server)

    await client.async_connect("connect.sid=egal")
    try:
        assert client.connected is True
        # Frist verkürzen und Stille vortäuschen — sonst dauerte der Test die
        # vom Gerät gemeldeten 60 Sekunden.
        client._ping_timeout = 0.2
        client._last_rx = time.monotonic() - 10

        await asyncio.wait_for(client.async_wait_closed(), 5)
        assert client.connected is False
    finally:
        await client.async_disconnect()


@pytest.mark.parametrize("server", [False], indirect=True)
async def test_handshake_uebernimmt_fristen(server: str) -> None:
    """pingInterval und pingTimeout des Geräts werden übernommen."""
    client = SocketIO2Client(server)
    await client.async_connect("connect.sid=egal")
    try:
        assert client._ping_interval == pytest.approx(22.5)
        assert client._ping_timeout == pytest.approx(60.0)
    finally:
        await client.async_disconnect()


def _data_mit_messwert(alter: float) -> StratonData:
    data = StratonData()
    data.readings["dev:1"] = SpotReading(external_id="dev:1", temperature=25.9)
    data.readings_at = time.monotonic() - alter
    return data


def test_frische_telemetrie_gilt() -> None:
    data = _data_mit_messwert(alter=5.0)
    assert data.telemetry_stale is False
    assert data.max_temperature == 25.9


def test_veraltete_telemetrie_wird_verworfen() -> None:
    """Ein eingefrorener Messwert darf nicht als gültige Temperatur gelten."""
    data = _data_mit_messwert(alter=3600.0)
    assert data.telemetry_stale is True
    # Der Wächter erhält None und hält damit eine bestehende Absenkung, statt
    # auf einer stundenalten Messung weiterzuregeln.
    assert data.max_temperature is None


def test_ohne_jede_messung_ist_die_telemetrie_veraltet() -> None:
    assert StratonData().telemetry_stale is True

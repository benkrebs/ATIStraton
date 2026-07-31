"""Minimaler Socket.IO-2.x-Client (Engine.IO 3) auf Basis von aiohttp.

Das Gerät spricht **Socket.IO 2.x / Engine.IO 3** — verifiziert daran, dass es auf
``EIO=3`` und ``EIO=4`` identisch mit Engine.IO-3-Framing (``00 09 07 ff``)
antwortet und ``pingInterval``/``pingTimeout`` die 2.x-Vorgaben 25000/60000 meldet.

``python-socketio`` 5.x spricht ausschließlich Engine.IO 4; der Handshake passt
damit nicht, und das Gerät verwirft ``emit``-Aufrufe stillschweigend. Ein Pin auf
``python-socketio==4.6.1`` wäre möglich, würde aber die gemeinsame
Python-Umgebung von Home Assistant beschädigen, sobald eine andere Integration
Version 5 benötigt. Dieser Client verwendet deshalb nur ``aiohttp``, das Home
Assistant ohnehin mitbringt.

Protokoll (nur der benötigte Ausschnitt):

===== ==========================================
``0`` Engine.IO OPEN, Nutzlast ist der Handshake
``2`` Engine.IO PING  — in EIO3 sendet der Client
``3`` Engine.IO PONG  — Antwort des Servers
``4`` Engine.IO MESSAGE, enthält ein Socket.IO-Paket
===== ==========================================

Innerhalb einer MESSAGE:

====== ===================================
``0``  Socket.IO CONNECT
``1``  Socket.IO DISCONNECT
``2``  Socket.IO EVENT — ``42["name",data]``
``4``  Socket.IO ERROR
====== ===================================
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

EIO_OPEN = "0"
EIO_CLOSE = "1"
EIO_PING = "2"
EIO_PONG = "3"
EIO_MESSAGE = "4"

SIO_CONNECT = "0"
SIO_DISCONNECT = "1"
SIO_EVENT = "2"
SIO_ERROR = "4"

CONNECT_TIMEOUT = 15.0

# Optionaler Namespace und optionale Ack-Nummer vor der JSON-Nutzlast.
_PACKET_PREFIX = re.compile(r"^(?P<namespace>/[^,]*,)?(?P<ack>\d+)?(?P<payload>[\[{].*)?$")

EventHandler = Callable[..., Awaitable[None] | None]


class SocketIO2Error(Exception):
    """Fehler in der Socket.IO-2.x-Verbindung."""


class SocketIO2Client:
    """Schlanker Client für Socket.IO 2.x über WebSocket."""

    def __init__(self, base_url: str, session: aiohttp.ClientSession | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._handlers: dict[str, EventHandler] = {}
        self._reader: asyncio.Task[None] | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self._ping_interval = 25.0
        self._closing = False

    @property
    def connected(self) -> bool:
        return (
            self._ws is not None and not self._ws.closed and self._connected.is_set()
        )

    def on(self, event: str, handler: EventHandler) -> None:
        """Registriert einen Handler für ein Ereignis."""
        self._handlers[event] = handler

    def _ws_url(self) -> str:
        parts = urlsplit(self._base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return f"{scheme}://{parts.netloc}/socket.io/?EIO=3&transport=websocket"

    async def async_connect(self, cookie: str) -> None:
        """Baut die Verbindung auf und wartet auf das CONNECT des Servers."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        self._closing = False
        self._connected.clear()

        try:
            self._ws = await self._session.ws_connect(
                self._ws_url(),
                headers={"Cookie": cookie},
                heartbeat=None,
                autoping=False,
            )
        except aiohttp.ClientError as err:
            raise SocketIO2Error(f"WebSocket-Verbindung fehlgeschlagen: {err}") from err

        self._reader = asyncio.create_task(self._read_loop())
        try:
            await asyncio.wait_for(self._connected.wait(), CONNECT_TIMEOUT)
        except TimeoutError as err:
            await self.async_disconnect()
            raise SocketIO2Error("Server hat den Handshake nicht bestätigt") from err

        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        _LOGGER.debug("Socket.IO 2.x verbunden mit %s", self._base_url)

    async def async_disconnect(self) -> None:
        """Beendet die Verbindung und räumt die Hintergrundaufgaben ab."""
        self._closing = True
        self._connected.clear()

        for task in (self._heartbeat, self._reader):
            if task is not None and not task.done():
                task.cancel()
        self._heartbeat = None
        self._reader = None

        if self._ws is not None and not self._ws.closed:
            with contextlib.suppress(Exception):
                await self._ws.send_str(EIO_CLOSE)
            await self._ws.close()
        self._ws = None

        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def async_emit(self, event: str, data: Any = None) -> None:
        """Sendet ein Ereignis an den Server."""
        if not self.connected:
            raise SocketIO2Error(f"Nicht verbunden, {event!r} nicht gesendet")
        args = [event] if data is None else [event, data]
        assert self._ws is not None
        await self._ws.send_str(f"{EIO_MESSAGE}{SIO_EVENT}{json.dumps(args)}")
        _LOGGER.debug("emit %s %s", event, data)

    async def _heartbeat_loop(self) -> None:
        """In Engine.IO 3 sendet der **Client** das Ping."""
        try:
            while not self._closing and self._ws is not None and not self._ws.closed:
                await asyncio.sleep(self._ping_interval)
                if self._ws.closed:
                    break
                await self._ws.send_str(EIO_PING)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - Heartbeat darf nie hochschlagen
            _LOGGER.debug("Heartbeat beendet: %s", err)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if message.type is aiohttp.WSMsgType.TEXT:
                    await self._handle(message.data)
                elif message.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - Leseschleife darf nie hochschlagen
            _LOGGER.debug("Leseschleife beendet: %s", err)
        finally:
            self._connected.clear()

    async def _handle(self, raw: str) -> None:
        if not raw:
            return
        packet_type, body = raw[0], raw[1:]

        if packet_type == EIO_OPEN:
            self._apply_handshake(body)
            return
        if packet_type == EIO_PONG:
            return
        if packet_type == EIO_PING:
            # Sollte in EIO3 nicht vorkommen, der Vollständigkeit halber.
            if self._ws is not None and not self._ws.closed:
                await self._ws.send_str(EIO_PONG)
            return
        if packet_type == EIO_CLOSE:
            self._connected.clear()
            return
        if packet_type != EIO_MESSAGE or not body:
            return

        await self._handle_socketio(body[0], body[1:])

    def _apply_handshake(self, body: str) -> None:
        try:
            handshake = json.loads(body)
        except ValueError:
            _LOGGER.debug("Handshake nicht lesbar: %r", body)
            return
        # pingInterval kommt in Millisekunden; etwas früher senden als gefordert.
        interval = handshake.get("pingInterval")
        if isinstance(interval, (int, float)) and interval > 0:
            self._ping_interval = max(1.0, interval / 1000.0 * 0.9)

    async def _handle_socketio(self, sio_type: str, body: str) -> None:
        if sio_type == SIO_CONNECT:
            self._connected.set()
            return
        if sio_type == SIO_DISCONNECT:
            self._connected.clear()
            return
        if sio_type == SIO_ERROR:
            _LOGGER.warning("Server meldet einen Socket.IO-Fehler: %s", body)
            return
        if sio_type != SIO_EVENT:
            return

        match = _PACKET_PREFIX.match(body)
        payload = match.group("payload") if match else None
        if not payload:
            return
        try:
            args = json.loads(payload)
        except ValueError:
            _LOGGER.debug("Ereignis-Nutzlast nicht lesbar: %r", payload)
            return
        if not isinstance(args, list) or not args:
            return

        event, *rest = args
        if not isinstance(event, str):
            return
        handler = self._handlers.get(event)
        if handler is None:
            return
        try:
            result = handler(*rest)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            _LOGGER.exception("Handler für %s ist gescheitert", event)

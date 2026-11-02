"""Asyncio TCP client for the Rako Hub (BASIC V3 protocol, port 9762).

Keeps one persistent connection, serialises request/response commands,
dispatches feedback/tracker events to callbacks, and reconnects with backoff.
No Home Assistant imports.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from . import protocol as P

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 9762
REQUEST_TIMEOUT = 10
KEEPALIVE_SECONDS = 60
RECONNECT_MIN = 2
RECONNECT_MAX = 60


class RakoHubError(Exception):
    """Base error."""


class RakoHubConnectionError(RakoHubError):
    """Could not connect / connection lost."""


class RakoHubClient:
    def __init__(self, host: str, port: int = DEFAULT_PORT, client_name: str = "HomeAssistant") -> None:
        self.host = host
        self.port = port
        self.client_name = client_name
        self.status: P.HubStatus | None = None
        self.connected = False
        self.last_feedback: float | None = None
        self.last_rx: float | None = None

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._rx_task: asyncio.Task | None = None
        self._run_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()            # one request in flight at a time
        self._pending: asyncio.Future | None = None
        self._pending_kind: str | None = None  # "status" | "query" | "send"
        self._pending_rows: list[list[str]] = []
        self._stop = False

        self._feedback_cbs: list[Callable[[P.Feedback], None]] = []
        self._tracker_cbs: list[Callable[[P.Tracker], None]] = []
        self._conn_cbs: list[Callable[[bool], None]] = []

    # ---- callbacks -------------------------------------------------------
    def on_feedback(self, cb: Callable[[P.Feedback], None]) -> None:
        self._feedback_cbs.append(cb)

    def on_tracker(self, cb: Callable[[P.Tracker], None]) -> None:
        self._tracker_cbs.append(cb)

    def on_connection(self, cb: Callable[[bool], None]) -> None:
        self._conn_cbs.append(cb)

    # ---- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        """Connect once (raises on failure) then keep the link alive in the background."""
        self._stop = False
        await self._connect()
        self._run_task = asyncio.create_task(self._supervise(), name="rako_hub_supervise")

    async def stop(self) -> None:
        self._stop = True
        for t in (self._run_task, self._keepalive_task, self._rx_task):
            if t:
                t.cancel()
        await self._close()

    async def _connect(self) -> None:
        _LOGGER.debug("Connecting to Rako hub %s:%s", self.host, self.port)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), REQUEST_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as err:
            raise RakoHubConnectionError(f"Cannot connect to {self.host}:{self.port}: {err}") from err
        self._rx_task = asyncio.create_task(self._rx_loop(), name="rako_hub_rx")
        await self._write(P.SUB_LINE.format(client=self.client_name))
        # SUB reply is "A,1"; it is consumed by the rx loop as a plain ack.
        await asyncio.sleep(0.2)
        self.status = await self.get_status()
        self.connected = True
        self.last_rx = time.time()
        for cb in self._conn_cbs:
            cb(True)
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(self._keepalive(), name="rako_hub_keepalive")
        _LOGGER.info("Connected to Rako hub %s (fw %s, id %s)", self.host,
                     self.status.firmware, self.status.hub_id)

    async def _close(self) -> None:
        was = self.connected
        self.connected = False
        if self._writer:
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), 2)
            except Exception:  # noqa: BLE001
                pass
        self._writer = None
        self._reader = None
        if self._pending and not self._pending.done():
            self._pending.set_exception(RakoHubConnectionError("connection closed"))
        if was:
            for cb in self._conn_cbs:
                cb(False)

    async def _supervise(self) -> None:
        delay = RECONNECT_MIN
        while not self._stop:
            if self._rx_task is not None:
                try:
                    await self._rx_task
                except asyncio.CancelledError:
                    return
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("rx loop ended: %s", err)
            await self._close()
            if self._stop:
                return
            _LOGGER.warning("Rako hub link lost; reconnecting in %ss", delay)
            await asyncio.sleep(delay)
            try:
                await self._connect()
                delay = RECONNECT_MIN
            except RakoHubConnectionError as err:
                _LOGGER.debug("Reconnect failed: %s", err)
                self._rx_task = None
                delay = min(delay * 2, RECONNECT_MAX)

    async def _keepalive(self) -> None:
        while not self._stop:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            if not self.connected:
                continue
            try:
                await self.get_status()
            except RakoHubError as err:
                _LOGGER.debug("keepalive failed: %s", err)
                if self._writer:
                    self._writer.close()

    # ---- I/O -------------------------------------------------------------------
    async def _write(self, line: str) -> None:
        if not self._writer:
            raise RakoHubConnectionError("not connected")
        _LOGGER.debug(">> %s", line)
        self._writer.write((line + "\r\n").encode("utf-8"))
        await self._writer.drain()

    async def _rx_loop(self) -> None:
        assert self._reader is not None
        buf = b""
        while True:
            chunk = await self._reader.read(4096)
            if not chunk:
                raise RakoHubConnectionError("EOF from hub")
            buf += chunk
            # lines may end in \r, \n or \r\n
            buf = buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        self.last_rx = time.time()
        _LOGGER.debug("<< %s", line)
        try:
            fields = P.split_csv(line)
        except Exception:  # noqa: BLE001
            fields = line.split(",")
        tag = fields[0]
        if tag == "F":
            try:
                fb = P.parse_feedback(fields)
            except (ValueError, IndexError):
                return
            self.last_feedback = time.time()
            for cb in self._feedback_cbs:
                cb(fb)
            return
        if tag == "T":
            try:
                tr = P.parse_tracker(fields)
            except (ValueError, IndexError):
                return
            for cb in self._tracker_cbs:
                cb(tr)
            return
        if tag == "A":
            return  # SUB ack
        pending = self._pending
        if pending is None or pending.done():
            return
        if tag == "STATUS" and self._pending_kind == "status":
            pending.set_result(P.HubStatus.parse(fields))
        elif tag == "SEND" and self._pending_kind == "send":
            pending.set_result(fields[1] == "1" if len(fields) > 1 else False)
        elif tag == "AERROR":
            pending.set_exception(RakoHubError(",".join(fields[1:]) or "error"))
        elif self._pending_kind == "query":
            if tag == "QUERY_HEADER":
                return
            if tag == "QUERY_COMPLETE":
                pending.set_result(self._pending_rows)
            else:
                self._pending_rows.append(fields)

    async def _request(self, kind: str, line: str) -> Any:
        async with self._lock:
            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            self._pending_kind = kind
            self._pending_rows = []
            try:
                await self._write(line)
                return await asyncio.wait_for(self._pending, REQUEST_TIMEOUT)
            except asyncio.TimeoutError as err:
                raise RakoHubError(f"timeout waiting for {kind}") from err
            finally:
                self._pending = None
                self._pending_kind = None

    # ---- API -------------------------------------------------------------------
    async def get_status(self) -> P.HubStatus:
        return await self._request("status", P.STATUS_LINE)

    async def query_rooms(self) -> dict[int, P.Room]:
        rows = await self._request("query", P.encode_query("ROOM", 0))
        rooms: dict[int, P.Room] = {}
        for r in rows:
            try:
                room = P.parse_room_row(r)
            except (ValueError, IndexError):
                continue
            rooms[room.room_id] = room
        return rooms

    async def query_channels(self, rooms: dict[int, P.Room]) -> None:
        rows = await self._request("query", P.encode_query("CHANNEL", 0))
        for r in rows:
            try:
                room_id, title, rtype, ch = P.parse_channel_row(r)
            except (ValueError, IndexError):
                continue
            room = rooms.setdefault(room_id, P.Room(room_id=room_id, title=title, type=rtype))
            room.channels[ch.channel_id] = ch

    async def query_levels(self, rooms: dict[int, P.Room]) -> None:
        rows = await self._request("query", P.encode_query("LEVEL", 0))
        for r in rows:
            try:
                room_id, ch_id, scene, cur, tgt = P.parse_level_row(r)
            except (ValueError, IndexError):
                continue
            room = rooms.get(room_id)
            if room is None:
                continue
            if ch_id == 0:
                room.current_scene = scene
            else:
                room.levels[ch_id] = (cur, tgt)
                if scene is not None and room.current_scene is None:
                    room.current_scene = scene

    async def send(self, room: int, channel: int, command: str, *args: int) -> bool:
        return await self._request("send", P.encode_send(room, channel, command, *args))

    async def set_scene(self, room: int, scene: int, channel: int = 0) -> bool:
        return await self.send(room, channel, "SCENE", int(scene))

    async def set_level(self, room: int, channel: int, level: int) -> bool:
        return await self.send(room, channel, "LEVEL", max(0, min(255, int(level))))

    async def fade(self, room: int, channel: int, direction: str) -> bool:
        cmd = {"up": "FADE_UP", "down": "FADE_DOWN", "stop": "FADE_STOP"}[direction]
        return await self.send(room, channel, cmd)

    async def set_rgb(self, room: int, channel: int, r: int, g: int, b: int) -> bool:
        return await self.send(room, channel, "RGB", r, g, b)

    async def store_scene(self, room: int, scene: int, channel: int = 0) -> bool:
        """EXPERIMENTAL: ask the hub to store current levels as a scene. Not in the public doc."""
        return await self.send(room, channel, "STORE", int(scene))

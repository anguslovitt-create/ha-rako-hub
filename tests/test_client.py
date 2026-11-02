"""Tests for the Rako Hub client against a scripted fake hub (no HA, no network)."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

from rako_hub import protocol as P  # noqa: E402
from rako_hub.client import RakoHubClient, RakoHubError  # noqa: E402


# ---------------- protocol parsing ----------------

def test_split_csv_quotes():
    assert P.split_csv('QUERY,13,"One, Room",LIGHT,0') == ["QUERY", "13", "One, Room", "LIGHT", "0"]


def test_parse_status():
    s = P.HubStatus.parse(P.split_csv("STATUS,Hub,V3,353e89dd-862c-3256-aa79-8c3b89c23f21,70:B3:D5:08:40:00,3.0.6,0"))
    assert s.firmware == "3.0.6" and s.mac == "70:B3:D5:08:40:00" and s.product == "Hub"


def test_parse_room_row_with_mode():
    r = P.parse_room_row(P.split_csv("QUERY,17,Master Bedroom,LIGHT,S4OFF"))
    assert r.room_id == 17 and r.title == "Master Bedroom" and r.mode == "4 scenes + off"


def test_parse_channel_row_both_shapes():
    row = "17,Master Bedroom,Lights,1,Ceiling,SLIDER,79,191,127,63,0,0,0,0,0,0,0,0,0,0,0,0"
    rid, _, _, ch = P.parse_channel_row(P.split_csv(row))
    assert rid == 17 and ch.channel_id == 1 and ch.title == "Ceiling" and ch.scene_levels[:4] == [79, 191, 127, 63]
    rid2, _, _, ch2 = P.parse_channel_row(P.split_csv("QUERY," + row))
    assert rid2 == 17 and ch2.scene_levels == ch.scene_levels


def test_parse_level_row():
    assert P.parse_level_row(P.split_csv("18,2,-1,50,50,0")) == (18, 2, None, 50, 50)
    assert P.parse_level_row(P.split_csv("QUERY,18,0,3,0,0,0")) == (18, 0, 3, 0, 0)


def test_parse_feedback_and_tracker():
    fb = P.parse_feedback(P.split_csv("F,17,0,SCENE,1"))
    assert (fb.room, fb.channel, fb.command, fb.argument) == (17, 0, "SCENE", 1)
    tr = P.parse_tracker(P.split_csv("T,85,8,LEV,150,123,168,0"))
    assert tr.kind == "LEVEL" and tr.current == 150 and tr.target == 123 and tr.temporary is False


def test_encode():
    assert P.encode_send(4, 1, "scene", 5) == "SEND,4,1,SCENE,5"
    assert P.encode_send(5, 4, "RGB", 127, 0, 255) == "SEND,5,4,RGB,127,0,255"
    assert P.encode_query("ROOM", 0) == "QUERY,ROOM,0,N"


# ---------------- fake hub ----------------

STATUS = "STATUS,Hub,V3,d3693dc9-3217-3fa7-9ebf-e610377fb1e8,70:B3:D5:08:40:00,3.1.5,0"
ROOMS = ["QUERY,9,GFLiving,LIGHT,S4OFF", 'QUERY,13,"Snug, Rear",LIGHT,0', "QUERY_COMPLETE,2"]
CHANNELS = [
    "9,GFLiving,LIGHT,1,Downlights,SLIDER,255,102,0,255,0,0,0,0,0,0,0,0,0,0,0,0",
    "9,GFLiving,LIGHT,2,Pendants,SLIDER,255,0,0,128,0,0,0,0,0,0,0,0,0,0,0,0",
    "13,Snug,LIGHT,1,Lamps,SLIDER,200,100,50,0,0,0,0,0,0,0,0,0,0,0,0,0",
    "QUERY_COMPLETE,3",
]
LEVELS = ["9,0,2,0,0,0", "9,1,2,102,102,0", "9,2,2,0,0,0", "13,1,-1,50,50,0", "QUERY_COMPLETE,4"]


async def fake_hub(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, log: list[str], inject: asyncio.Queue):
    async def send_lines(lines):
        for ln in lines:
            writer.write((ln + "\r\n").encode())
        await writer.drain()

    async def injector():
        while True:
            lines = await inject.get()
            await send_lines(lines)

    inj = asyncio.create_task(injector())
    try:
        while True:
            raw = await reader.readline()
            if not raw:
                break
            line = raw.decode().strip()
            log.append(line)
            f = line.split(",")
            if f[0] == "SUB":
                await send_lines(["A,1"])
            elif f[0] == "STATUS":
                await send_lines([STATUS])
            elif f[0] == "QUERY" and f[1] == "ROOM":
                await send_lines(["QUERY_HEADER,ROOM_ID,ROOM_TITLE,ROOM_TYPE", *ROOMS])
            elif f[0] == "QUERY" and f[1] == "CHANNEL":
                await send_lines(CHANNELS)
            elif f[0] == "QUERY" and f[1] == "LEVEL":
                await send_lines(LEVELS)
            elif f[0] == "SEND":
                if f[3] == "STORE":
                    await send_lines(["AERROR,UNKNOWN_ERROR"])
                else:
                    # feedback + ack + tracker, interleaved like a real hub
                    await send_lines([f"F,{f[1]},{f[2]},{f[3]},{f[4] if len(f) > 4 else ''}", "SEND,1",
                                      f"T,{f[1]},{f[2]},LEVEL,0,{f[4] if len(f) > 4 else 0},300,0"])
    finally:
        inj.cancel()


async def _hub_impl():
    log: list[str] = []
    inject: asyncio.Queue = asyncio.Queue()
    server = await asyncio.start_server(lambda r, w: fake_hub(r, w, log, inject), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, log, inject
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_connect_status_and_queries(hub):
    port, log, _ = hub
    c = RakoHubClient("127.0.0.1", port, client_name="TestHA")
    await c.start()
    try:
        assert c.connected and c.status.firmware == "3.1.5"
        assert log[0] == "SUB,BASIC,V3,TestHA,TF"
        rooms = await c.query_rooms()
        assert set(rooms) == {9, 13} and rooms[13].title == "Snug, Rear" and rooms[9].mode == "4 scenes + off"
        await c.query_channels(rooms)
        assert rooms[9].channels[2].title == "Pendants" and rooms[9].channels[1].scene_levels[1] == 102
        await c.query_levels(rooms)
        assert rooms[9].current_scene == 2 and rooms[9].levels[1] == (102, 102)
        assert rooms[13].current_scene is None and rooms[13].levels[1] == (50, 50)
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_send_dispatches_feedback_and_tracker(hub):
    port, log, _ = hub
    c = RakoHubClient("127.0.0.1", port)
    fbs, trs = [], []
    c.on_feedback(fbs.append)
    c.on_tracker(trs.append)
    await c.start()
    try:
        assert await c.set_level(9, 1, 300) is True  # clamped to 255
        assert log[-1] == "SEND,9,1,LEVEL,255"
        await asyncio.sleep(0.1)
        assert fbs and fbs[0].room == 9 and fbs[0].command == "LEVEL" and fbs[0].argument == 255
        assert trs and trs[0].target == 255 and c.last_feedback is not None
        assert await c.set_scene(9, 3) is True
        assert log[-1] == "SEND,9,0,SCENE,3"
        with pytest.raises(RakoHubError):
            await c.store_scene(9, 3)
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_keypad_press_arrives_unsolicited(hub):
    port, _, inject = hub
    c = RakoHubClient("127.0.0.1", port)
    fbs = []
    c.on_feedback(fbs.append)
    await c.start()
    try:
        await inject.put(["F,9,0,SCENE,1", "T,9,1,LEVEL,0,255,1000,0"])
        await asyncio.sleep(0.1)
        assert fbs and fbs[0].room == 9 and fbs[0].command == "SCENE" and fbs[0].argument == 1
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_reconnect_after_drop(hub, monkeypatch):
    import rako_hub.client as cl
    monkeypatch.setattr(cl, "RECONNECT_MIN", 0.05)
    port, _, _ = hub
    c = RakoHubClient("127.0.0.1", port)
    states = []
    c.on_connection(states.append)
    await c.start()
    try:
        assert states == [True]
        c._writer.close()  # simulate the hub dropping us
        for _ in range(50):
            await asyncio.sleep(0.05)
            if states[-3:] == [True, False, True]:
                break
        assert states[-1] is True and False in states
        assert (await c.get_status()).firmware == "3.1.5"
    finally:
        await c.stop()

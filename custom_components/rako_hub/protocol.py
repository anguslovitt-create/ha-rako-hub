"""Pure-Python parser/encoder for the Rako Hub BASIC V3 TCP protocol (port 9762).

No Home Assistant imports here so it can be unit-tested standalone.
Reference: Rako "Accessing the Rako Hub" API document.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field


SUB_LINE = "SUB,BASIC,V3,{client},TF"
STATUS_LINE = "STATUS,0"

ROOM_MODES = {"0": "default", "S4OFF": "4 scenes + off", "S7OFF": "7 scenes + off",
              "S16OFF": "16 scenes + off", "SNAMEDOFF": "named scenes + off"}


def split_csv(line: str) -> list[str]:
    """Split one protocol line honouring double-quoted fields."""
    return next(csv.reader(io.StringIO(line), skipinitialspace=False))


@dataclass
class HubStatus:
    product: str
    protocol: str
    hub_id: str
    mac: str
    firmware: str

    @classmethod
    def parse(cls, fields: list[str]) -> "HubStatus":
        # STATUS,Hub,V3,<uuid>,<mac>,<fw>,0
        return cls(product=fields[1], protocol=fields[2], hub_id=fields[3],
                   mac=fields[4], firmware=fields[5])


@dataclass
class Channel:
    channel_id: int
    title: str
    type: str
    scene_levels: list[int] = field(default_factory=list)  # S1..S16


@dataclass
class Room:
    room_id: int
    title: str
    type: str
    mode: str = "default"
    channels: dict[int, Channel] = field(default_factory=dict)
    current_scene: int | None = None
    levels: dict[int, tuple[int, int]] = field(default_factory=dict)  # channel -> (current, target)


@dataclass
class Feedback:
    """F line: a command seen on the Rako wire (keypad press, app, timer...)."""
    room: int
    channel: int
    command: str
    argument: int | None


@dataclass
class Tracker:
    """T line: a level/scene change in progress."""
    room: int
    channel: int
    kind: str          # LEVEL or SCENE
    current: int
    target: int
    time_ms: int
    temporary: bool


def parse_room_row(fields: list[str]) -> Room:
    # QUERY,<id>,<title>,<type>[,<mode>]
    mode = fields[4] if len(fields) > 4 else "0"
    return Room(room_id=int(fields[1]), title=fields[2], type=fields[3],
                mode=ROOM_MODES.get(mode, mode))


def parse_channel_row(fields: list[str]) -> tuple[int, str, str, Channel]:
    """Rows may or may not carry the leading 'QUERY' token; normalise."""
    if fields and fields[0] == "QUERY":
        fields = fields[1:]
    # ROOM_ID,ROOM_TITLE,ROOM_TYPE,CHANNEL_ID,CHANNEL_TITLE,CHANNEL_TYPE,S1..S16
    room_id = int(fields[0])
    levels = [int(x) for x in fields[6:22] if x != ""]
    ch = Channel(channel_id=int(fields[3]), title=fields[4], type=fields[5], scene_levels=levels)
    return room_id, fields[1], fields[2], ch


def parse_level_row(fields: list[str]) -> tuple[int, int, int | None, int, int]:
    if fields and fields[0] == "QUERY":
        fields = fields[1:]
    # ROOM_ID,CHANNEL_ID,CURRENT_SCENE,CURRENT_LEVEL,TARGET_LEVEL,RFU
    scene = int(fields[2])
    return int(fields[0]), int(fields[1]), (None if scene < 0 else scene), int(fields[3]), int(fields[4])


def parse_feedback(fields: list[str]) -> Feedback:
    # F,<room>,<channel>,<TYPE>[,<arg>]
    arg = int(fields[4]) if len(fields) > 4 and fields[4] != "" else None
    return Feedback(room=int(fields[1]), channel=int(fields[2]), command=fields[3], argument=arg)


def parse_tracker(fields: list[str]) -> Tracker:
    # T,<room>,<channel>,<TYPE>,<current>,<target>,<time_ms>,<temporary>
    kind = fields[3]
    if kind == "LEV":
        kind = "LEVEL"
    nums = [int(x) if x not in ("", None) else 0 for x in fields[4:8]]
    while len(nums) < 4:
        nums.append(0)
    return Tracker(room=int(fields[1]), channel=int(fields[2]), kind=kind,
                   current=nums[0], target=nums[1], time_ms=nums[2], temporary=bool(nums[3]))


def encode_send(room: int, channel: int, command: str, *args: int) -> str:
    parts = ["SEND", str(room), str(channel), command.upper(), *[str(a) for a in args]]
    return ",".join(parts)


def encode_query(kind: str, room: int = 0, no_header: bool = True) -> str:
    parts = ["QUERY", kind.upper(), str(room)]
    if no_header:
        parts.append("N")
    return ",".join(parts)

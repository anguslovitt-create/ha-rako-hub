"""Holds hub structure (rooms/channels/scene levels) and live state; bridges events to HA."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import protocol as P
from .client import RakoHubClient, RakoHubError
from .const import DOMAIN, EVENT_FEEDBACK, EVENT_TRACKER, STRUCTURE_REFRESH_HOURS

_LOGGER = logging.getLogger(__name__)
SIGNAL_STATE = f"{DOMAIN}_state"


class RakoHubCoordinator(DataUpdateCoordinator[dict[int, P.Room]]):
    """Periodically re-reads structure; live level/scene updates come from tracker events."""

    def __init__(self, hass: HomeAssistant, client: RakoHubClient, entry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(hours=STRUCTURE_REFRESH_HOURS))
        self.client = client
        self.entry_id = entry.entry_id
        self.rooms: dict[int, P.Room] = {}
        client.on_feedback(self._on_feedback)
        client.on_tracker(self._on_tracker)
        client.on_connection(self._on_connection)

    async def _async_update_data(self) -> dict[int, P.Room]:
        try:
            rooms = await self.client.query_rooms()
            await self.client.query_channels(rooms)
            await self.client.query_levels(rooms)
        except RakoHubError as err:
            raise UpdateFailed(str(err)) from err
        # keep live values we already learned for rooms not reported by LEVEL yet
        for rid, old in self.rooms.items():
            new = rooms.get(rid)
            if new is None:
                continue
            if new.current_scene is None:
                new.current_scene = old.current_scene
            for ch, lv in old.levels.items():
                new.levels.setdefault(ch, lv)
        self.rooms = rooms
        return rooms

    # ---- live events (called from the client's rx task, inside the HA loop) ----
    @callback
    def _on_feedback(self, fb: P.Feedback) -> None:
        room = self.rooms.get(fb.room)
        chan = room.channels.get(fb.channel) if room else None
        self.hass.bus.async_fire(EVENT_FEEDBACK, {
            "room_id": fb.room,
            "room": room.title if room else None,
            "channel_id": fb.channel,
            "channel": chan.title if chan else ("all" if fb.channel == 0 else None),
            "command": fb.command,
            "value": fb.argument,
        })
        if room is not None and fb.command == "SCENE" and fb.channel == 0 and fb.argument is not None:
            room.current_scene = fb.argument
            # scene applied: project stored levels onto channels for an immediate state
            for cid, ch in room.channels.items():
                if 1 <= fb.argument <= len(ch.scene_levels):
                    lvl = ch.scene_levels[fb.argument - 1]
                    room.levels[cid] = (lvl, lvl)
                elif fb.argument == 0:
                    room.levels[cid] = (0, 0)
            async_dispatcher_send(self.hass, SIGNAL_STATE)
        async_dispatcher_send(self.hass, f"{SIGNAL_STATE}_feedback")

    @callback
    def _on_tracker(self, tr: P.Tracker) -> None:
        room = self.rooms.get(tr.room)
        if room is not None:
            if tr.kind == "SCENE":
                room.current_scene = tr.target if tr.target else tr.current
            else:
                room.levels[tr.channel] = (tr.current, tr.target)
                if tr.channel != 0 and not tr.temporary:
                    room.current_scene = None if room.current_scene is None else room.current_scene
        self.hass.bus.async_fire(EVENT_TRACKER, {
            "room_id": tr.room, "room": room.title if room else None,
            "channel_id": tr.channel, "kind": tr.kind, "current": tr.current,
            "target": tr.target, "time_ms": tr.time_ms, "temporary": tr.temporary,
        })
        async_dispatcher_send(self.hass, SIGNAL_STATE)

    @callback
    def _on_connection(self, up: bool) -> None:
        async_dispatcher_send(self.hass, f"{SIGNAL_STATE}_link", up)
        if up:
            # re-sync levels after a reconnect
            self.hass.async_create_task(self.async_request_refresh())

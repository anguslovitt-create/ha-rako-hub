"""Sensors: last feedback timestamp, and one 'scene' sensor per room with stored scene levels."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol as P
from .binary_sensor import hub_device_info
from .const import DOMAIN
from .coordinator import SIGNAL_STATE, RakoHubCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: RakoHubCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [RakoLastFeedbackSensor(coordinator)]
    entities += [RakoRoomSceneSensor(coordinator, rid) for rid in sorted(coordinator.rooms)]
    add(entities)


class RakoLastFeedbackSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Last activity"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False
    _attr_icon = "mdi:pulse"

    def __init__(self, coordinator: RakoHubCoordinator) -> None:
        self._c = coordinator
        self._attr_unique_id = f"{coordinator.entry_id}_last_feedback"
        self._attr_device_info = hub_device_info(coordinator)
        self._last: dict | None = None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{SIGNAL_STATE}_feedback", self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        ts = self._c.client.last_feedback
        return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


class RakoRoomSceneSensor(SensorEntity):
    """State = current scene number for the room (0 = off); attributes carry the programming."""
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:lightbulb-group-outline"

    def __init__(self, coordinator: RakoHubCoordinator, room_id: int) -> None:
        self._c = coordinator
        self._rid = room_id
        room = coordinator.rooms[room_id]
        self._attr_name = f"{room.title} scene"
        self._attr_unique_id = f"{coordinator.entry_id}_room_{room_id}_scene"
        self._attr_device_info = hub_device_info(coordinator)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_STATE, self._update))
        self.async_on_remove(self._c.async_add_listener(self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()

    @property
    def _room(self) -> P.Room | None:
        return self._c.rooms.get(self._rid)

    @property
    def available(self) -> bool:
        return self._room is not None and self._c.client.connected

    @property
    def native_value(self) -> int | None:
        room = self._room
        return room.current_scene if room else None

    @property
    def extra_state_attributes(self) -> dict:
        room = self._room
        if room is None:
            return {}
        channels = []
        for cid, ch in sorted(room.channels.items()):
            cur = room.levels.get(cid)
            channels.append({
                "id": cid, "title": ch.title, "type": ch.type,
                "level": cur[0] if cur else None,
                "target": cur[1] if cur else None,
                "scene_levels": {f"S{i + 1}": lvl for i, lvl in enumerate(ch.scene_levels) if lvl or i < 4},
            })
        return {
            "room_id": room.room_id, "room_type": room.type, "mode": room.mode,
            "channels": channels,
        }

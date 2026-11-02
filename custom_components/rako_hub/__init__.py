"""Rako Hub observability integration: hub link, scene levels, keypad events, raw control."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import (CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN, SERVICE_FADE, SERVICE_REFRESH,
                    SERVICE_SET_LEVEL, SERVICE_SET_RGB, SERVICE_SET_SCENE, SERVICE_STORE_SCENE)

if TYPE_CHECKING:  # keep the package importable without Home Assistant (unit tests)
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["binary_sensor", "sensor"]


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415

    from .client import RakoHubClient, RakoHubError  # noqa: PLC0415
    from .coordinator import RakoHubCoordinator  # noqa: PLC0415

    client = RakoHubClient(entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT), client_name="HomeAssistant")
    coordinator = RakoHubCoordinator(hass, client, entry)
    try:
        await client.start()
    except RakoHubError as err:
        raise HomeAssistantError(f"Rako hub unreachable: {err}") from err
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.client.stop()
    return ok


def _first_coordinator(hass: "HomeAssistant"):
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415

    coords = list(hass.data.get(DOMAIN, {}).values())
    if not coords:
        raise HomeAssistantError("Rako hub not set up")
    return coords[0]


def _register_services(hass: "HomeAssistant") -> None:
    import voluptuous as vol  # noqa: PLC0415
    from homeassistant.core import ServiceCall  # noqa: PLC0415
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415
    from homeassistant.helpers import config_validation as cv  # noqa: PLC0415

    from .client import RakoHubError  # noqa: PLC0415

    if hass.services.has_service(DOMAIN, SERVICE_SET_SCENE):
        return
    ROOM = vol.Required("room")
    CHANNEL = vol.Optional("channel", default=0)

    async def _wrap(call: ServiceCall, fn) -> None:
        try:
            ok = await fn(_first_coordinator(hass).client)
        except RakoHubError as err:
            raise HomeAssistantError(f"Rako hub: {err}") from err
        if ok is False:
            raise HomeAssistantError("Rako hub rejected the command")

    async def set_scene(call: ServiceCall) -> None:
        await _wrap(call, lambda c: c.set_scene(call.data["room"], call.data["scene"], call.data["channel"]))

    async def set_level(call: ServiceCall) -> None:
        await _wrap(call, lambda c: c.set_level(call.data["room"], call.data["channel"], call.data["level"]))

    async def fade(call: ServiceCall) -> None:
        await _wrap(call, lambda c: c.fade(call.data["room"], call.data["channel"], call.data["direction"]))

    async def set_rgb(call: ServiceCall) -> None:
        await _wrap(call, lambda c: c.set_rgb(call.data["room"], call.data["channel"],
                                              call.data["red"], call.data["green"], call.data["blue"]))

    async def store_scene(call: ServiceCall) -> None:
        await _wrap(call, lambda c: c.store_scene(call.data["room"], call.data["scene"], call.data["channel"]))

    async def refresh(call: ServiceCall) -> None:
        await _first_coordinator(hass).async_request_refresh()

    u8 = vol.All(vol.Coerce(int), vol.Range(0, 255))
    hass.services.async_register(DOMAIN, SERVICE_SET_SCENE, set_scene, schema=vol.Schema({
        ROOM: cv.positive_int, CHANNEL: cv.positive_int,
        vol.Required("scene"): vol.All(vol.Coerce(int), vol.Range(0, 16))}))
    hass.services.async_register(DOMAIN, SERVICE_SET_LEVEL, set_level, schema=vol.Schema({
        ROOM: cv.positive_int, vol.Required("channel"): cv.positive_int, vol.Required("level"): u8}))
    hass.services.async_register(DOMAIN, SERVICE_FADE, fade, schema=vol.Schema({
        ROOM: cv.positive_int, CHANNEL: cv.positive_int,
        vol.Required("direction"): vol.In(["up", "down", "stop"])}))
    hass.services.async_register(DOMAIN, SERVICE_SET_RGB, set_rgb, schema=vol.Schema({
        ROOM: cv.positive_int, CHANNEL: cv.positive_int,
        vol.Required("red"): u8, vol.Required("green"): u8, vol.Required("blue"): u8}))
    hass.services.async_register(DOMAIN, SERVICE_STORE_SCENE, store_scene, schema=vol.Schema({
        ROOM: cv.positive_int, CHANNEL: cv.positive_int,
        vol.Required("scene"): vol.All(vol.Coerce(int), vol.Range(1, 16))}))
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, refresh)

"""Hub link binary sensor."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SIGNAL_STATE, RakoHubCoordinator


def hub_device_info(coordinator: RakoHubCoordinator) -> DeviceInfo:
    st = coordinator.client.status
    return DeviceInfo(
        identifiers={(DOMAIN, st.hub_id if st else coordinator.entry_id)},
        name="Rako Hub",
        manufacturer="Rako Controls",
        model=st.product if st else "Hub",
        sw_version=st.firmware if st else None,
        connections={("mac", st.mac.lower())} if st and st.mac else set(),
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: RakoHubCoordinator = hass.data[DOMAIN][entry.entry_id]
    add([RakoHubLinkSensor(coordinator)])


class RakoHubLinkSensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Link"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_should_poll = False

    def __init__(self, coordinator: RakoHubCoordinator) -> None:
        self._c = coordinator
        self._attr_unique_id = f"{coordinator.entry_id}_link"
        self._attr_device_info = hub_device_info(coordinator)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{SIGNAL_STATE}_link", self._update))

    @callback
    def _update(self, _up: bool) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._c.client.connected

    @property
    def extra_state_attributes(self) -> dict:
        st = self._c.client.status
        return {"host": self._c.client.host, "firmware": st.firmware if st else None,
                "hub_id": st.hub_id if st else None, "rooms": len(self._c.rooms)}

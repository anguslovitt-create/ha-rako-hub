"""Config flow for Rako Hub."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .client import RakoHubClient, RakoHubError
from .const import CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN


class RakoHubConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = RakoHubClient(user_input[CONF_HOST], user_input[CONF_PORT], client_name="HomeAssistant-setup")
            try:
                await client.start()
                status = client.status
            except RakoHubError:
                errors["base"] = "cannot_connect"
            else:
                await client.stop()
                await self.async_set_unique_id(status.hub_id)
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(
                    title=f"Rako Hub ({user_input[CONF_HOST]}, fw {status.firmware})", data=user_input)
            finally:
                if client.connected:
                    await client.stop()

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

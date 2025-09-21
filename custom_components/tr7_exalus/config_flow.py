"""Config flow for TR7 Exalus integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
import websockets
from websockets.exceptions import WebSocketException

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_EMAIL, CONF_PASSWORD, DEFAULT_PORT, DOMAIN, WS_API_PATH

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    host = data[CONF_HOST]
    email = data[CONF_EMAIL]
    password = data[CONF_PASSWORD]

    # Test WebSocket connection
    try:
        uri = f"ws://{host}:{DEFAULT_PORT}{WS_API_PATH}"
        _LOGGER.debug("Testing connection to %s", uri)

        # Try to connect and immediately close with timeout
        websocket = await asyncio.wait_for(
            websockets.connect(uri),
            timeout=10
        )
        await websocket.close()

        _LOGGER.info("Successfully connected to TR7 Exalus at %s", host)

    except (OSError, WebSocketException, asyncio.TimeoutError) as err:
        _LOGGER.error("Cannot connect to TR7 Exalus at %s: %s", host, err)
        raise CannotConnect from err

    # Return info that you want to store in the config entry.
    return {
        "title": f"TR7 Exalus ({host})",
        "host": host,
        "email": email,
        "password": password,
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TR7 Exalus."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Check if already configured
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
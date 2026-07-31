"""Config Flow der ATI Straton Integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import StratonApiClient, StratonAuthError, StratonConnectionError
from .const import (
    CONF_MAX_INTENSITY,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .intensity import MAX_INTENSITY, MIN_INTENSITY

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class StratonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Einrichtung über die Oberfläche."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await self._async_validate(user_input)
            except StratonAuthError:
                errors["base"] = "invalid_auth"
            except StratonConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unerwarteter Fehler bei der Einrichtung")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(str(info["id"]))
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(
                    title=info.get("deviceType") or "ATI Straton", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            candidate = {**entry.data, **user_input}
            try:
                await self._async_validate(candidate)
            except StratonAuthError:
                errors["base"] = "invalid_auth"
            except StratonConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data=candidate)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def _async_validate(self, user_input: dict[str, Any]) -> dict[str, Any]:
        """Meldet sich probeweise an und liest die Geräteidentität."""
        client = StratonApiClient(
            user_input[CONF_HOST],
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
            session=async_create_clientsession(self.hass),
        )
        await client.async_login()
        info = await client.async_get("info")
        if not isinstance(info, dict) or "id" not in info:
            raise StratonConnectionError("Gerät lieferte keine verwertbare Identität")
        return info

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> StratonOptionsFlow:
        return StratonOptionsFlow()


class StratonOptionsFlow(OptionsFlow):
    """Abfrageintervall und Obergrenze der Intensität."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                ),
                vol.Required(
                    CONF_MAX_INTENSITY,
                    default=options.get(CONF_MAX_INTENSITY, MAX_INTENSITY),
                ): vol.All(
                    vol.Coerce(float),
                    vol.Range(min=MIN_INTENSITY, max=MAX_INTENSITY),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

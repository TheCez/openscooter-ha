"""Config flow: one click to add, options for smart charging."""
from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ENERGY, CONF_OFF_MIN, CONF_OFF_W, CONF_PLUG, CONF_POWER, CONF_SECRET, CONF_START_BELOW,
    CONF_TARGET, CONF_WEBHOOK_ID, DEFAULT_OFF_MIN, DEFAULT_OFF_W, DEFAULT_START_BELOW,
    DEFAULT_TARGET, DOMAIN,
)


class OpenScooterConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(
                title="OpenScooter",
                data={CONF_WEBHOOK_ID: webhook.async_generate_id(), CONF_SECRET: secrets.token_urlsafe(24)},
            )
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return OpenScooterOptionsFlow()


def _num(lo: float, hi: float, unit: str | None = None, step: float = 1) -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=lo, max=hi, step=step, mode=selector.NumberSelectorMode.BOX, unit_of_measurement=unit))


class OpenScooterOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            for k in (CONF_TARGET, CONF_START_BELOW, CONF_OFF_W, CONF_OFF_MIN):
                if k in user_input:
                    user_input[k] = int(user_input[k])
            return self.async_create_entry(title="", data=user_input)
        o = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_PLUG, description={"suggested_value": o.get(CONF_PLUG)}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            vol.Optional(CONF_POWER, description={"suggested_value": o.get(CONF_POWER)}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="power")),
            vol.Optional(CONF_ENERGY, description={"suggested_value": o.get(CONF_ENERGY)}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
            vol.Required(CONF_TARGET, default=o.get(CONF_TARGET, DEFAULT_TARGET)): _num(50, 100, "%"),
            vol.Required(CONF_START_BELOW, default=o.get(CONF_START_BELOW, DEFAULT_START_BELOW)): _num(10, 100, "%"),
            vol.Required(CONF_OFF_W, default=o.get(CONF_OFF_W, DEFAULT_OFF_W)): _num(1, 200, "W"),
            vol.Required(CONF_OFF_MIN, default=o.get(CONF_OFF_MIN, DEFAULT_OFF_MIN)): _num(1, 60, "min"),
        })
        return self.async_show_form(step_id="init", data_schema=schema)

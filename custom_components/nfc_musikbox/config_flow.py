"""Config Flow, Lesegeräte (Subentries) und Optionen."""

from __future__ import annotations

from typing import Any, override

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
import voluptuous as vol

from .const import (
    CONF_DEVICE_ID,
    CONF_PLAYER,
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_CARD_CHANGE_DELAY,
    OPT_QUEUE_JUMP_TIMEOUT,
    OPT_REMOVAL_REWIND,
    OPT_RESTORE_SETTLE,
    OPT_RESTORE_START_TIMEOUT,
    OPT_SEEK_ATTEMPTS,
    OPT_SEEK_RETRY_DELAY,
    OPT_SEEK_TOLERANCE,
    OPT_SKIP_BACK,
    SUBENTRY_READER,
)
from .reader import ESPHOME_DOMAIN, device_title, find_card_sensor

TITLE = "NFC-Musikbox"


class NfcMusikboxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ein einziger Eintrag; Lesegeräte werden als Subentries ergänzt."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NfcMusikboxOptionsFlow()

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_READER: ReaderSubentryFlow}


def _player_selector() -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain="media_player"))


class ReaderSubentryFlow(ConfigSubentryFlow):
    """Lesegerät (ESPHome-Gerät) mit seinem Lautsprecher verbinden."""

    def _used_devices(self, exclude_subentry: str | None = None) -> set[str]:
        return {
            sub.data[CONF_DEVICE_ID]
            for sub in self._get_entry().subentries.values()
            if sub.subentry_type == SUBENTRY_READER and sub.subentry_id != exclude_subentry
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID]
            if device_id in self._used_devices():
                errors[CONF_DEVICE_ID] = "already_configured"
            elif find_card_sensor(self.hass, device_id) is None:
                errors[CONF_DEVICE_ID] = "no_card_sensor"
            else:
                return self.async_create_entry(
                    title=device_title(self.hass, device_id),
                    data=user_input,
                    unique_id=device_id,
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID): DeviceSelector(
                    DeviceSelectorConfig(integration=ESPHOME_DOMAIN)
                ),
                vol.Required(CONF_PLAYER): _player_selector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(self._get_entry(), subentry, data_updates=user_input)
        schema = vol.Schema({vol.Required(CONF_PLAYER): _player_selector()})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, subentry.data),
            description_placeholders={"reader": subentry.title},
        )


def _seconds(minimum: float, maximum: float, step: float = 0.1) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement="s",
            mode=NumberSelectorMode.BOX,
        )
    )


OPTIONS_SELECTORS: dict[str, NumberSelector] = {
    OPT_CARD_CHANGE_DELAY: _seconds(0, 5),
    OPT_REMOVAL_REWIND: _seconds(0, 10),
    OPT_SKIP_BACK: _seconds(5, 120, 1),
    OPT_RESTORE_SETTLE: _seconds(0, 10),
    OPT_RESTORE_START_TIMEOUT: _seconds(5, 60, 1),
    OPT_QUEUE_JUMP_TIMEOUT: _seconds(2, 60, 1),
    OPT_SEEK_TOLERANCE: _seconds(1, 60, 1),
    OPT_SEEK_ATTEMPTS: NumberSelector(
        NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)
    ),
    OPT_SEEK_RETRY_DELAY: _seconds(0.5, 10),
}


class NfcMusikboxOptionsFlow(OptionsFlow):
    """Zeiten und Toleranzen für Fortsetzen und Tasten."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            data = dict(user_input)
            data[OPT_SEEK_ATTEMPTS] = int(data[OPT_SEEK_ATTEMPTS])
            return self.async_create_entry(data=data)
        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(key, default=current[key]): selector
                for key, selector in OPTIONS_SELECTORS.items()
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

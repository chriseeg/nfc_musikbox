"""Taste "Tageslimits zurücksetzen"."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NfcMusikboxConfigEntry, NfcMusikboxData
from .const import hub_device_identifier


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NfcMusikboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ResetLimitsButton(entry.runtime_data, entry.entry_id)])


class ResetLimitsButton(ButtonEntity):
    """Setzt Anzahl und Hörzeit von heute auf null."""

    _attr_has_entity_name = True
    _attr_translation_key = "reset_limits"
    _attr_should_poll = False

    def __init__(self, data: NfcMusikboxData, entry_id: str) -> None:
        self._data = data
        self._attr_unique_id = f"{entry_id}_reset_limits"
        self._attr_device_info = DeviceInfo(identifiers={hub_device_identifier(entry_id)})

    async def async_press(self) -> None:
        self._data.limits.reset()

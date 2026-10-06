"""Verbindungsstatus pro Lesegerät."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import NfcMusikboxConfigEntry
from .const import DOMAIN
from .reader import ReaderConfig, reader_online


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NfcMusikboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for reader in entry.runtime_data.readers.values():
        async_add_entities([ReaderConnected(reader)], config_subentry_id=reader.subentry_id)


class ReaderConnected(BinarySensorEntity):
    """An, solange das Lesegerät mit Home Assistant verbunden ist."""

    _attr_has_entity_name = True
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, reader: ReaderConfig) -> None:
        self._reader = reader
        self._attr_unique_id = f"{reader.subentry_id}_connected"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, reader.subentry_id)})

    @property
    def is_on(self) -> bool:
        return reader_online(self.hass, self._reader)

    async def async_added_to_hass(self) -> None:
        if self._reader.card_sensor is None:
            return

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(self.hass, self._reader.card_sensor, _changed)
        )

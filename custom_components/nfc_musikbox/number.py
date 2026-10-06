"""Startlautstärke pro Lesegerät."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NfcMusikboxConfigEntry, NfcMusikboxData
from .const import DOMAIN
from .reader import ReaderConfig
from .store import StoreEvent


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NfcMusikboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    for reader in data.readers.values():
        async_add_entities([ReaderStartVolume(data, reader)], config_subentry_id=reader.subentry_id)


class ReaderStartVolume(NumberEntity):
    """Lautstärke, mit der eine aufgelegte Karte startet (0 = nicht ändern)."""

    _attr_has_entity_name = True
    _attr_translation_key = "start_volume"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, data: NfcMusikboxData, reader: ReaderConfig) -> None:
        self._data = data
        self._reader_id = reader.subentry_id
        self._attr_unique_id = f"{reader.subentry_id}_start_volume"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, reader.subentry_id)})

    @property
    def native_value(self) -> float:
        return self._data.store.get_reader_settings(self._reader_id).start_volume

    async def async_set_native_value(self, value: float) -> None:
        await self._data.store.async_set_reader_settings(self._reader_id, start_volume=round(value))

    async def async_added_to_hass(self) -> None:
        @callback
        def _changed(event: StoreEvent, key: str) -> None:
            if event is StoreEvent.READER and key == self._reader_id:
                self.async_write_ha_state()

        self.async_on_remove(self._data.store.async_add_listener(_changed))

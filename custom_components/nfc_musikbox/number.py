"""Einstellungen pro Lesegerät als Zahl: Start-/Maximallautstärke, Schlaf-Timer."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NfcMusikboxConfigEntry, NfcMusikboxData
from .const import DOMAIN
from .reader import ReaderConfig
from .store import StoreEvent


@dataclass(frozen=True, slots=True)
class ReaderNumberSpec:
    key: str  # Feld in ReaderSettings und translation_key
    maximum: int
    step: int
    unit: str


SPECS = (
    ReaderNumberSpec("start_volume", 100, 5, PERCENTAGE),
    ReaderNumberSpec("max_volume", 100, 5, PERCENTAGE),
    ReaderNumberSpec("sleep_timer", 180, 5, UnitOfTime.MINUTES),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NfcMusikboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    for reader in data.readers.values():
        async_add_entities(
            [ReaderNumber(data, reader, spec) for spec in SPECS],
            config_subentry_id=reader.subentry_id,
        )


class ReaderNumber(NumberEntity):
    """Zahl-Einstellung eines Lesegeräts (0 = Funktion aus bzw. nicht ändern)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    _attr_native_min_value = 0
    _attr_mode = NumberMode.SLIDER

    def __init__(self, data: NfcMusikboxData, reader: ReaderConfig, spec: ReaderNumberSpec) -> None:
        self._data = data
        self._reader_id = reader.subentry_id
        self._key = spec.key
        self._attr_translation_key = spec.key
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_unique_id = f"{reader.subentry_id}_{spec.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, reader.subentry_id)})

    @property
    def native_value(self) -> float:
        value: int = getattr(self._data.store.get_reader_settings(self._reader_id), self._key)
        return value

    async def async_set_native_value(self, value: float) -> None:
        await self._data.store.async_set_reader_settings(
            self._reader_id, **{self._key: round(value)}
        )

    async def async_added_to_hass(self) -> None:
        @callback
        def _changed(event: StoreEvent, key: str) -> None:
            if event is StoreEvent.READER and key == self._reader_id:
                self.async_write_ha_state()

        self.async_on_remove(self._data.store.async_add_listener(_changed))

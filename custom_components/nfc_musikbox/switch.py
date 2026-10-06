"""Schalter "Aktiv" pro Karte."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NfcMusikboxConfigEntry, NfcMusikboxData
from .const import DOMAIN
from .entity import CardEntity, async_setup_card_entities
from .reader import ReaderConfig
from .store import StoreEvent


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NfcMusikboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    for reader in data.readers.values():
        async_add_entities(
            [ReaderParentalSwitch(data, reader)], config_subentry_id=reader.subentry_id
        )
    async_setup_card_entities(
        hass,
        entry,
        async_add_entities,
        lambda data, card: [CardEnabledSwitch(data, card, "enabled")],
    )


class CardEnabledSwitch(CardEntity, SwitchEntity):
    """Deaktivierte Karten reagieren nicht auf das Auflegen."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        card = self.card
        return card.enabled if card else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._data.store.async_update_card(self._tag_id, enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._data.store.async_update_card(self._tag_id, enabled=False)


class ReaderParentalSwitch(SwitchEntity):
    """Hauptschalter der Kindersicherung eines Lesegeräts."""

    _attr_has_entity_name = True
    _attr_translation_key = "parental"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, data: NfcMusikboxData, reader: ReaderConfig) -> None:
        self._data = data
        self._reader_id = reader.subentry_id
        self._attr_unique_id = f"{reader.subentry_id}_parental"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, reader.subentry_id)})

    @property
    def is_on(self) -> bool:
        return self._data.store.get_reader_settings(self._reader_id).parental

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._data.store.async_set_reader_settings(self._reader_id, parental=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._data.store.async_set_reader_settings(self._reader_id, parental=False)

    async def async_added_to_hass(self) -> None:
        @callback
        def _changed(event: StoreEvent, key: str) -> None:
            if event is StoreEvent.READER and key == self._reader_id:
                self.async_write_ha_state()

        self.async_on_remove(self._data.store.async_add_listener(_changed))

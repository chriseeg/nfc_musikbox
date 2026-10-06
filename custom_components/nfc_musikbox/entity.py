"""Basis für Entitäten pro Karte (ein Gerät pro Karte)."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NfcMusikboxConfigEntry, NfcMusikboxData
from .const import SIGNAL_CARD_ADDED, card_device_identifier
from .store import Card, StoreEvent


class CardEntity(Entity):
    """Entität, die zu einer Karte gehört und Store-Änderungen folgt."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _update_events: frozenset[StoreEvent] = frozenset({StoreEvent.CARD_UPDATED})

    def __init__(self, data: NfcMusikboxData, card: Card, key: str) -> None:
        self._data = data
        self._tag_id = card.tag_id
        self._attr_translation_key = key
        self._attr_unique_id = f"card_{card.tag_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={card_device_identifier(card.tag_id)},
            name=card.name,
            manufacturer="NFC-Musikbox",
            model="NFC-Karte",
            serial_number=card.tag_id,
        )

    @property
    def card(self) -> Card | None:
        return self._data.store.cards.get(self._tag_id)

    @property
    def available(self) -> bool:
        return self.card is not None

    async def async_added_to_hass(self) -> None:
        @callback
        def _changed(event: StoreEvent, tag_id: str) -> None:
            if tag_id == self._tag_id and event in self._update_events:
                self.async_write_ha_state()

        self.async_on_remove(self._data.store.async_add_listener(_changed))


def async_setup_card_entities(
    hass: HomeAssistant,
    entry: NfcMusikboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[NfcMusikboxData, Card], Iterable[CardEntity]],
) -> None:
    """Entitäten für vorhandene und später angelegte Karten erzeugen."""
    data = entry.runtime_data
    async_add_entities(
        [entity for card in data.store.cards.values() for entity in factory(data, card)]
    )

    @callback
    def _card_added(tag_id: str) -> None:
        card = data.store.cards.get(tag_id)
        if card is not None:
            async_add_entities(list(factory(data, card)))

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_CARD_ADDED, _card_added))

"""Diagnose-Sensor pro Lesegerät."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import NfcMusikboxConfigEntry, NfcMusikboxData
from .const import DOMAIN, NO_CARD
from .entity import CardEntity, async_setup_card_entities
from .players import fmt_position
from .reader import ReaderConfig
from .store import StoreEvent


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NfcMusikboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    for reader in data.readers.values():
        async_add_entities([ReaderCardSensor(data, reader)], config_subentry_id=reader.subentry_id)
    async_setup_card_entities(
        hass, entry, async_add_entities, lambda d, card: [CardPositionSensor(d, card, "position")]
    )


class ReaderCardSensor(SensorEntity):
    """Zeigt, welche Karte gerade auf dem Lesegerät liegt, und die Zuordnung."""

    _attr_has_entity_name = True
    _attr_translation_key = "current_card"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, data: NfcMusikboxData, reader: ReaderConfig) -> None:
        self._data = data
        self._reader = reader
        self._attr_unique_id = f"{reader.subentry_id}_current_card"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, reader.subentry_id)})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reader = self._reader
        return {
            "card_sensor": reader.card_sensor,
            "play_event": reader.play_event,
            "back_event": reader.back_event,
            "led_action": f"esphome.{reader.led_action}" if reader.led_action else None,
            "media_player": reader.player,
        }

    async def async_added_to_hass(self) -> None:
        if self._reader.card_sensor is None:
            self._attr_available = False
            return
        self._update_from(self.hass.states.get(self._reader.card_sensor))
        self.async_on_remove(
            async_track_state_change_event(self.hass, self._reader.card_sensor, self._handle_change)
        )

    @callback
    def _handle_change(self, event: Event[EventStateChangedData]) -> None:
        self._update_from(event.data["new_state"])
        self.async_write_ha_state()

    @callback
    def _update_from(self, state: Any) -> None:
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._attr_native_value = None
            return
        tag_id: str = state.state
        if tag_id == NO_CARD:
            self._attr_native_value = NO_CARD
            return
        card = self._data.store.cards.get(tag_id)
        self._attr_native_value = card.name if card else tag_id


class CardPositionSensor(CardEntity, SensorEntity):
    """Gemerkte Stelle einer Tonie-Karte, z. B. "Titel 2 · 1:10:10 · Kapitel 2"."""

    _update_events = frozenset({StoreEvent.CARD_UPDATED, StoreEvent.POSITION})

    @property
    def native_value(self) -> str | None:
        position = self._data.store.positions.get(self._tag_id)
        return fmt_position(position) if position else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        position = self._data.store.positions.get(self._tag_id)
        if position is None:
            return {}
        return {"queue_position": position.q, "seconds": position.p, "title": position.t}

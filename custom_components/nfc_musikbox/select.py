"""Auswahl "Betriebsart" (Tonie/Einfach) pro Karte."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NfcMusikboxConfigEntry, NfcMusikboxData
from .entity import CardEntity, async_setup_card_entities
from .store import CARD_MODES, Card


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NfcMusikboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_setup_card_entities(
        hass, entry, async_add_entities, lambda data, card: [CardModeSelect(data, card, "mode")]
    )


class CardModeSelect(CardEntity, SelectEntity):
    """Tonie: läuft nur, solange die Karte liegt. Einfach: startet nur."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data: NfcMusikboxData, card: Card, key: str) -> None:
        super().__init__(data, card, key)
        self._attr_options = list(CARD_MODES)

    @property
    def current_option(self) -> str | None:
        card = self.card
        return card.mode if card else None

    async def async_select_option(self, option: str) -> None:
        await self._data.store.async_update_card(self._tag_id, mode=option)

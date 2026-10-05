"""Schalter "Aktiv" pro Karte."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NfcMusikboxConfigEntry
from .entity import CardEntity, async_setup_card_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NfcMusikboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
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

"""Diagnose-Ausgabe (Einstellungen → Geräte & Dienste → NFC-Musikbox → Diagnose)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import NfcMusikboxConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NfcMusikboxConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    readers = {}
    for reader in data.readers.values():
        card_state = hass.states.get(reader.card_sensor) if reader.card_sensor else None
        player_state = hass.states.get(reader.player)
        readers[reader.subentry_id] = {
            **asdict(reader),
            "card_sensor_state": card_state.state if card_state else None,
            "player_state": player_state.state if player_state else None,
            "player_attributes": dict(player_state.attributes) if player_state else None,
        }
    return {
        "options": data.options,
        "readers": readers,
        "cards": [asdict(card) for card in data.store.cards.values()],
        "positions": {tag: asdict(pos) for tag, pos in data.store.positions.items()},
    }

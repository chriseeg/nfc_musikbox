"""Dienste assign_card, remove_card, reset_position."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.nfc_musikbox.const import DOMAIN
from custom_components.nfc_musikbox.store import Position

from .conftest import CARD_SENSOR, PLAYER, make_entry, make_reader
from .fake_player import FakeSonos
from .test_scanner import FAST_OPTIONS, settle

MEDIA = {
    "entity_id": PLAYER,
    "media_content_id": "FV:2/46",
    "media_content_type": "favorite_item_id",
    "metadata": {"title": "Hörspiel Puderzucker", "thumbnail": None},
}


async def _setup(hass: HomeAssistant):
    entry = make_entry(make_reader(hass), options=FAST_OPTIONS)
    hass.states.async_set(CARD_SENSOR, "none")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def test_assign_then_place_plays(hass: HomeAssistant) -> None:
    player = FakeSonos(hass, PLAYER).register()
    entry = await _setup(hass)

    await hass.services.async_call(
        DOMAIN, "assign_card", {"tag_id": " ca-09-0c-05 ", "media": MEDIA}, blocking=True
    )
    card = entry.runtime_data.store.cards["CA-09-0C-05"]
    assert card.name == "Hörspiel Puderzucker"
    assert card.mode == "tonie"
    assert card.readers == []

    hass.states.async_set(CARD_SENSOR, "CA-09-0C-05")
    await settle(hass)
    assert player.services("media_player.play_media") == [
        {"media_content_id": "FV:2/46", "media_content_type": "favorite_item_id"}
    ]


async def test_assign_with_reader_and_mode(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    subentry_id = next(iter(entry.subentries))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry_id), config_entry_id=entry.entry_id
    )
    assert device is not None

    await hass.services.async_call(
        DOMAIN,
        "assign_card",
        {"tag_id": "A", "name": "Test", "media": MEDIA, "mode": "simple", "readers": [device.id]},
        blocking=True,
    )
    card = entry.runtime_data.store.cards["A"]
    assert (card.name, card.mode, card.readers) == ("Test", "simple", [subentry_id])

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "assign_card",
            {"tag_id": "A", "media": MEDIA, "readers": ["keingeraet"]},
            blocking=True,
        )


async def test_reset_and_remove(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    store = entry.runtime_data.store
    await hass.services.async_call(
        DOMAIN, "assign_card", {"tag_id": "A", "media": MEDIA}, blocking=True
    )
    store.set_position("A", Position(q=2, p=5, t="x"))

    await hass.services.async_call(DOMAIN, "reset_position", {"tag_id": "a"}, blocking=True)
    assert "A" not in store.positions

    await hass.services.async_call(DOMAIN, "remove_card", {"tag_id": "A"}, blocking=True)
    assert "A" not in store.cards
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "remove_card", {"tag_id": "A"}, blocking=True)


async def test_services_without_entry(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "reset_position", {"tag_id": "A"}, blocking=True)

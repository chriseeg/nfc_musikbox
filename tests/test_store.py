"""Persistenz von Karten und Positionen."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nfc_musikbox.store import Card, CardStore, Position

MEDIA = {
    "media_content_id": "FV:2/46",
    "media_content_type": "favorite_item_id",
    "metadata": {"title": "Hörspiel Puderzucker"},
}


async def test_roundtrip(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    store = CardStore(hass)
    await store.async_load()
    await store.async_set_card(
        Card(tag_id="CA-09-0C-05", name="Puderzucker", media=MEDIA, readers=["r1"])
    )
    store.set_position("CA-09-0C-05", Position(q=2, p=4210, t="Kapitel 2"))
    await store.async_save()

    other = CardStore(hass)
    await other.async_load()
    card = other.cards["CA-09-0C-05"]
    assert card.media == MEDIA
    assert card.mode == "tonie"
    assert card.works_on("r1")
    assert not card.works_on("r2")
    assert other.positions["CA-09-0C-05"] == Position(q=2, p=4210, t="Kapitel 2")


async def test_position_delayed_save(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer: FrozenDateTimeFactory
) -> None:
    store = CardStore(hass)
    await store.async_load()
    store.set_position("A", Position(q=1, p=10, t="x"))
    assert "nfc_musikbox" not in hass_storage
    freezer.tick(10)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage["nfc_musikbox"]["data"]["positions"] == {"A": {"q": 1, "p": 10, "t": "x"}}


async def test_invalid_entries_ignored(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    hass_storage["nfc_musikbox"] = {
        "version": 1,
        "minor_version": 1,
        "key": "nfc_musikbox",
        "data": {
            "cards": [{"name": "ohne tag_id"}, {"tag_id": "B", "mode": "quatsch"}],
            "positions": {"B": {"q": "kein int"}},
        },
    }
    store = CardStore(hass)
    await store.async_load()
    assert list(store.cards) == ["B"]
    assert store.cards["B"].mode == "tonie"
    assert store.cards["B"].name == "B"
    assert store.positions == {}


def test_title_truncated() -> None:
    assert len(Position(q=1, p=0, t="x" * 100).t) == 60


async def test_remove_card_drops_position(hass: HomeAssistant) -> None:
    store = CardStore(hass)
    await store.async_load()
    await store.async_set_card(Card(tag_id="A", name="A"))
    store.set_position("A", Position(q=1, p=1, t=""))
    await store.async_remove_card("A")
    assert store.cards == {}
    assert store.positions == {}

"""Musik-Modus (Shuffle/Wiederholen), Hörspiel ohne Shuffle, Startlautstärke."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.nfc_musikbox.const import DOMAIN
from custom_components.nfc_musikbox.store import Card, CardStore, Position

from .conftest import CARD_SENSOR, PLAYER, make_entry, make_reader
from .fake_player import ALL_FEATURES, FakeSonos
from .test_scanner import FAST_OPTIONS, MEDIA_A, card

A = "CA-09-0C-05"


@pytest.fixture
def player(hass: HomeAssistant) -> FakeSonos:
    return FakeSonos(hass, PLAYER, features=ALL_FEATURES).register()


async def setup(hass: HomeAssistant, *cards: Card) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = make_entry(make_reader(hass), options=FAST_OPTIONS)
    hass.states.async_set(CARD_SENSOR, "none")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    for c in cards:
        await entry.runtime_data.store.async_set_card(c)
    return entry


def reader_id(entry: MockConfigEntry) -> str:
    return next(iter(entry.subentries))


async def test_music_mode_applies_shuffle_and_repeat_before_play(
    hass: HomeAssistant, player: FakeSonos
) -> None:
    await setup(hass, Card(A, "M", MEDIA_A, mode="simple", shuffle=True, repeat="all"))
    await card(hass, A)
    assert player.service_names == [
        "media_player.shuffle_set",
        "media_player.repeat_set",
        "media_player.play_media",
    ]
    assert player.services("media_player.shuffle_set") == [{"shuffle": True}]
    assert player.services("media_player.repeat_set") == [{"repeat": "all"}]


async def test_music_mode_unchanged_settings(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass, Card(A, "M", MEDIA_A, mode="simple"))
    await card(hass, A)
    assert player.service_names == ["media_player.play_media"]


async def test_audiobook_turns_shuffle_off_before_play(
    hass: HomeAssistant, player: FakeSonos
) -> None:
    # Gespeichertes Shuffle/Repeat einer Hörspielkarte wird ignoriert
    await setup(hass, Card(A, "H", MEDIA_A, shuffle=True, repeat="one"))
    await card(hass, A)
    assert player.service_names == ["media_player.shuffle_set", "media_player.play_media"]
    assert player.services("media_player.shuffle_set") == [{"shuffle": False}]


async def test_audiobook_resume_paused_does_not_touch_shuffle(
    hass: HomeAssistant, player: FakeSonos
) -> None:
    entry = await setup(hass, Card(A, "H", MEDIA_A))
    entry.runtime_data.store.set_position(A, Position(q=2, p=100, t="Kapitel 2"))
    player.set_paused(q=2, pos=100)
    await card(hass, A)
    assert player.service_names == ["media_player.media_play"]


async def test_start_volume_set_first(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, Card(A, "M", MEDIA_A, mode="simple"))
    await entry.runtime_data.store.async_set_reader_settings(reader_id(entry), start_volume=40)
    await card(hass, A)
    assert player.service_names == ["media_player.volume_set", "media_player.play_media"]
    assert player.services("media_player.volume_set") == [{"volume_level": 0.4}]


async def test_start_volume_also_on_resume(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, Card(A, "H", MEDIA_A))
    await entry.runtime_data.store.async_set_reader_settings(reader_id(entry), start_volume=25)
    entry.runtime_data.store.set_position(A, Position(q=2, p=100, t="Kapitel 2"))
    player.set_paused(q=2, pos=100)
    await card(hass, A)
    assert player.service_names == ["media_player.volume_set", "media_player.media_play"]


async def test_unsupported_features_skipped(hass: HomeAssistant) -> None:
    player = FakeSonos(hass, PLAYER, features=0).register()
    entry = await setup(hass, Card(A, "M", MEDIA_A, mode="simple", shuffle=True, repeat="all"))
    await entry.runtime_data.store.async_set_reader_settings(reader_id(entry), start_volume=40)
    await card(hass, A)
    assert player.service_names == ["media_player.play_media"]


async def test_start_volume_number_entity_and_ws(
    hass: HomeAssistant, player: FakeSonos, hass_ws_client: WebSocketGenerator
) -> None:
    entry = await setup(hass)
    rid = reader_id(entry)
    number_id = er.async_get(hass).async_get_entity_id("number", DOMAIN, f"{rid}_start_volume")
    assert number_id is not None
    assert hass.states.get(number_id).state == "0"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 35}, blocking=True
    )
    assert entry.runtime_data.store.get_reader_settings(rid).start_volume == 35

    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {"type": f"{DOMAIN}/reader/update", "reader": rid, "start_volume": 60}
    )
    assert (await ws.receive_json())["success"]
    await hass.async_block_till_done()
    assert hass.states.get(number_id).state == "60"

    await ws.send_json_auto_id({"type": f"{DOMAIN}/subscribe"})
    await ws.receive_json()
    snap = (await ws.receive_json())["event"]
    assert snap["readers"][0]["start_volume"] == 60

    await ws.send_json_auto_id(
        {"type": f"{DOMAIN}/reader/update", "reader": "x", "start_volume": 1}
    )
    assert not (await ws.receive_json())["success"]


async def test_ws_save_card_with_music_settings(
    hass: HomeAssistant, player: FakeSonos, hass_ws_client: WebSocketGenerator
) -> None:
    entry = await setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {
            "type": f"{DOMAIN}/card/save",
            "tag_id": A,
            "name": "M",
            "media": MEDIA_A,
            "mode": "simple",
            "shuffle": True,
            "repeat": "one",
        }
    )
    assert (await ws.receive_json())["success"]
    c = entry.runtime_data.store.cards[A]
    assert (c.shuffle, c.repeat) == (True, "one")

    await ws.send_json_auto_id(
        {
            "type": f"{DOMAIN}/card/save",
            "tag_id": A,
            "name": "M",
            "media": MEDIA_A,
            "repeat": "quatsch",
        }
    )
    assert not (await ws.receive_json())["success"]


async def test_store_roundtrip_new_fields(hass: HomeAssistant) -> None:
    store = CardStore(hass)
    await store.async_load()
    await store.async_set_card(Card(A, "M", MEDIA_A, mode="simple", shuffle=False, repeat="off"))
    await store.async_set_reader_settings("r1", start_volume=150)
    other = CardStore(hass)
    await other.async_load()
    assert (other.cards[A].shuffle, other.cards[A].repeat) == (False, "off")
    assert other.get_reader_settings("r1").start_volume == 100
    assert other.get_reader_settings("unbekannt").start_volume == 0

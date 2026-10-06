"""Tasten (Play/Pause, Zurück kurz/lang) und LED-Status."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nfc_musikbox.store import Card, Position

from .conftest import BACK_EVENT, CARD_SENSOR, PLAY_EVENT, PLAYER, make_entry, make_reader
from .fake_player import FakeSonos
from .test_scanner import FAST_OPTIONS, MEDIA_A, settle

A = "CA-09-0C-05"
LED_SERVICE = "nfc_musikbox_set_playback_state"


@pytest.fixture
def player(hass: HomeAssistant) -> FakeSonos:
    return FakeSonos(hass, PLAYER).register()


@pytest.fixture
def led_calls(hass: HomeAssistant) -> list[str]:
    calls: list[str] = []

    async def _led(call: ServiceCall) -> None:
        calls.append(call.data["player_state"])

    hass.services.async_register("esphome", LED_SERVICE, _led)
    return calls


async def setup(hass: HomeAssistant, card_value: str = A) -> MockConfigEntry:
    reader = make_reader(hass)
    entry = make_entry(reader, options=FAST_OPTIONS)
    hass.states.async_set(CARD_SENSOR, card_value)
    hass.states.async_set(PLAY_EVENT, "unknown", {"event_type": None})
    hass.states.async_set(BACK_EVENT, "unknown", {"event_type": None})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await entry.runtime_data.store.async_set_card(Card(tag_id=A, name="A", media=MEDIA_A))
    await settle(hass)
    return entry


async def press(hass: HomeAssistant, entity_id: str, event_type: str, age: float = 0) -> None:
    fired = dt_util.utcnow() - timedelta(seconds=age)
    hass.states.async_set(
        entity_id, fired.isoformat(timespec="milliseconds"), {"event_type": event_type}
    )
    await settle(hass)


# ---------- Tasten ----------


async def test_play_button_toggles(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass)
    player.set_playing(q=1, pos=10)
    await press(hass, PLAY_EVENT, "kurz")
    assert player.service_names == ["media_player.media_play_pause"]
    assert player.state == "paused"


async def test_buttons_need_card(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass, card_value="none")
    player.set_playing(q=1, pos=100)
    await press(hass, PLAY_EVENT, "kurz")
    await press(hass, BACK_EVENT, "kurz")
    await press(hass, BACK_EVENT, "lang")
    assert player.service_names == []


@pytest.mark.parametrize(
    ("state", "pos", "expected"),
    [
        ("playing", 100, [{"seek_position": 70}]),
        ("paused", 20, [{"seek_position": 0}]),
        ("idle", 0, []),
    ],
)
async def test_back_short_skips_30s(
    hass: HomeAssistant, player: FakeSonos, state: str, pos: float, expected: list[Any]
) -> None:
    await setup(hass)
    if state == "playing":
        player.set_playing(q=1, pos=pos)
    elif state == "paused":
        player.set_paused(q=1, pos=pos)
    await press(hass, BACK_EVENT, "kurz")
    assert player.services("media_player.media_seek") == expected


async def test_back_long_restarts_queue(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass)
    player.set_playing(q=3, pos=500)
    await press(hass, BACK_EVENT, "lang")
    assert player.services("sonos.play_queue") == [{"queue_position": 0}]


async def test_back_long_generic_player_seeks_to_zero(hass: HomeAssistant) -> None:
    player = FakeSonos(hass, PLAYER).register(platform="cast")
    await setup(hass)
    player.set_playing(q=1, pos=500)
    await press(hass, BACK_EVENT, "lang")
    assert player.service_names == ["media_player.media_seek"]
    assert player.services("media_player.media_seek") == [{"seek_position": 0}]


async def test_stale_or_unavailable_events_ignored(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass)
    player.set_playing(q=1, pos=10)
    # Reconnect: unavailable, dann wiederhergestellter alter Zeitstempel
    hass.states.async_set(PLAY_EVENT, "unavailable")
    await settle(hass)
    await press(hass, PLAY_EVENT, "kurz", age=3600)
    assert player.service_names == []


async def test_first_press_after_unknown_counts(hass: HomeAssistant, player: FakeSonos) -> None:
    """Das Blueprint hat den ersten Druck nach HA-Start verschluckt."""
    await setup(hass)
    player.set_playing(q=1, pos=10)
    await press(hass, PLAY_EVENT, "kurz")
    assert player.service_names == ["media_player.media_play_pause"]


async def test_button_cancels_running_restore(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, card_value="none")
    entry.runtime_data.store.set_position(A, Position(q=2, p=4210, t="Kapitel 2"))
    entry.runtime_data.options["restore_start_timeout"] = 5
    player.start_delay = None  # Fortsetzen hängt beim Warten auf "playing"

    hass.states.async_set(CARD_SENSOR, A)
    await hass.async_block_till_done()
    player.set_playing(q=1, pos=3)
    player.calls.clear()
    player.start_delay = 0
    await press(hass, BACK_EVENT, "lang")

    # Nur "von vorne", kein nachträglicher Sprung zu Titel 2
    assert player.service_names == ["sonos.play_queue"]
    assert player.services("sonos.play_queue") == [{"queue_position": 0}]


# ---------- LED ----------


async def test_led_sent_on_setup_and_state_change(
    hass: HomeAssistant, player: FakeSonos, led_calls: list[str]
) -> None:
    await setup(hass)
    assert led_calls == ["idle"]

    player.set_playing(q=1, pos=0)
    await settle(hass)
    player.set_playing(q=1, pos=5)  # nur Attribute geändert
    await settle(hass)
    player.set_paused(q=1, pos=5)
    await settle(hass)
    hass.states.async_set(PLAYER, "buffering")
    await settle(hass)
    assert led_calls == ["idle", "playing", "paused", "playing"]


async def test_led_resent_when_reader_online(
    hass: HomeAssistant, player: FakeSonos, led_calls: list[str]
) -> None:
    entry = await setup(hass)
    reader = next(iter(entry.runtime_data.readers.values()))
    led_calls.clear()

    hass.bus.async_fire("esphome.nfc_reader_online", {"device_id": "anderes_geraet"})
    await settle(hass)
    assert led_calls == []

    hass.bus.async_fire(
        "esphome.nfc_reader_online", {"device_id": reader.device_id, "reader": "nfc-musikbox"}
    )
    await settle(hass)
    assert led_calls == ["idle"]


async def test_led_online_event_matched_by_node_name(
    hass: HomeAssistant, player: FakeSonos, led_calls: list[str]
) -> None:
    await setup(hass)
    led_calls.clear()
    hass.bus.async_fire("esphome.nfc_reader_online", {"reader": "nfc-musikbox"})
    await settle(hass)
    assert led_calls == ["idle"]


async def test_led_reader_offline_then_online(hass: HomeAssistant, player: FakeSonos) -> None:
    """Ohne registrierte ESPHome-Aktion (Lesegerät offline) kein Fehler, später Nachholen."""
    entry = await setup(hass)
    reader = next(iter(entry.runtime_data.readers.values()))
    player.set_playing(q=1, pos=0)
    await settle(hass)

    calls: list[str] = []

    async def _led(call: ServiceCall) -> None:
        calls.append(call.data["player_state"])

    hass.services.async_register("esphome", LED_SERVICE, _led)
    hass.bus.async_fire("esphome.nfc_reader_online", {"device_id": reader.device_id})
    await settle(hass)
    assert calls == ["playing"]

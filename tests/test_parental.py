"""Kindersicherung: Maximallautstärke, Schlaf-Timer, LED-Sperrsignal."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.nfc_musikbox import scanner
from custom_components.nfc_musikbox.const import DOMAIN
from custom_components.nfc_musikbox.store import Card

from .conftest import CARD_SENSOR, PLAY_EVENT, PLAYER, make_entry, make_reader
from .fake_player import ALL_FEATURES, FakeSonos
from .test_buttons_led import press
from .test_scanner import FAST_OPTIONS, MEDIA_A, MEDIA_B, card, settle

A = "CA-09-0C-05"
B = "8E-12-13-05"
LED_SERVICE = "nfc_musikbox_set_playback_state"


@pytest.fixture
def player(hass: HomeAssistant) -> FakeSonos:
    return FakeSonos(hass, PLAYER, features=ALL_FEATURES).register()


@pytest.fixture
def led_calls(hass: HomeAssistant) -> list[str]:
    calls: list[str] = []

    async def _led(call: ServiceCall) -> None:
        calls.append(call.data["player_state"])

    hass.services.async_register("esphome", LED_SERVICE, _led)
    return calls


@pytest.fixture(autouse=True)
def fast_fade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scanner, "SLEEP_FADE_SECONDS", 0.05)


async def setup(hass: HomeAssistant, **settings: Any) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = make_entry(make_reader(hass), options=FAST_OPTIONS)
    hass.states.async_set(CARD_SENSOR, "none")
    hass.states.async_set(PLAY_EVENT, "unknown", {"event_type": None})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    store = entry.runtime_data.store
    await store.async_set_card(Card(A, "Hörspiel", MEDIA_A))
    await store.async_set_card(Card(B, "Einschlafmusik", MEDIA_B, mode="simple"))
    if settings:
        await store.async_set_reader_settings(next(iter(entry.subentries)), **settings)
    await settle(hass)
    return entry


# ---------- Maximallautstärke ----------


async def test_max_volume_enforced_while_card_on_reader(
    hass: HomeAssistant, player: FakeSonos
) -> None:
    await setup(hass, max_volume=50)
    await card(hass, A)
    player.calls.clear()
    player.volume = 0.8
    player.publish()
    await settle(hass)
    assert player.services("media_player.volume_set") == [{"volume_level": 0.5}]

    # Ohne Karte (Eltern hören Musik) keine Grenze
    await card(hass, "none")
    player.calls.clear()
    player.volume = 0.9
    player.publish()
    await settle(hass)
    assert player.services("media_player.volume_set") == []


async def test_start_volume_capped_by_max(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass, start_volume=80, max_volume=50)
    await card(hass, A)
    assert player.services("media_player.volume_set")[0] == {"volume_level": 0.5}


async def test_loud_player_lowered_on_start_without_start_volume(
    hass: HomeAssistant, player: FakeSonos
) -> None:
    player.volume = 0.9
    player.publish()
    await setup(hass, max_volume=40)
    await card(hass, A)
    assert player.service_names[0] == "media_player.volume_set"
    assert player.services("media_player.volume_set")[0] == {"volume_level": 0.4}


# ---------- Schlaf-Timer ----------


async def test_sleep_timer_fades_pauses_and_blocks(
    hass: HomeAssistant, player: FakeSonos, led_calls: list[str]
) -> None:
    entry = await setup(hass, sleep_timer=5)
    await card(hass, A)
    player.set_playing(q=2, pos=100)
    player.calls.clear()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5, seconds=1))
    await settle(hass)
    volumes = [c["volume_level"] for c in player.services("media_player.volume_set")]
    assert volumes[-1] == 0.3  # alte Lautstärke zurück
    assert volumes[-2] == 0.0  # vorher ganz ausgeblendet
    assert "media_player.media_pause" in player.service_names
    assert entry.runtime_data.store.positions[A].q == 2

    # Play bleibt gesperrt, bis die Karte neu aufgelegt wird
    player.calls.clear()
    led_calls.clear()
    await press(hass, PLAY_EVENT, "kurz")
    assert player.service_names == []
    assert led_calls == ["locked"]

    await card(hass, "none")
    await card(hass, A)
    assert "media_player.media_play" in player.service_names or (
        "media_player.play_media" in player.service_names
    )


async def test_sleep_timer_cancelled_by_removal(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass, sleep_timer=5)
    await card(hass, A)
    await card(hass, "none")
    player.calls.clear()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await settle(hass)
    assert player.service_names == []


async def test_sleep_timer_off_by_default(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass)
    await card(hass, A)
    player.set_playing(q=1, pos=0)
    player.calls.clear()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=3))
    await settle(hass)
    assert player.service_names == []


# ---------- Entitäten und Websocket ----------


async def test_parental_entities(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass)
    rid = next(iter(entry.subentries))
    ent_reg = er.async_get(hass)
    switch_id = ent_reg.async_get_entity_id("switch", DOMAIN, f"{rid}_parental")
    max_id = ent_reg.async_get_entity_id("number", DOMAIN, f"{rid}_max_volume")
    timer_id = ent_reg.async_get_entity_id("number", DOMAIN, f"{rid}_sleep_timer")
    assert switch_id and max_id and timer_id
    assert hass.states.get(switch_id).state == "on"

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": max_id, "value": 45}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": timer_id, "value": 30}, blocking=True
    )
    settings = entry.runtime_data.store.get_reader_settings(rid)
    assert (settings.parental, settings.max_volume, settings.sleep_timer) == (False, 45, 30)

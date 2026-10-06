"""Tageslimits, Freigabe per Mitteilung und Live-Aktivität."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
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
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.nfc_musikbox import limits as limits_module, parents, scanner
from custom_components.nfc_musikbox.const import DOMAIN
from custom_components.nfc_musikbox.store import Card, DailyUsage

from .conftest import CARD_SENSOR, PLAY_EVENT, PLAYER, make_entry, make_reader
from .fake_player import ALL_FEATURES, FakeSonos
from .test_buttons_led import press
from .test_scanner import FAST_OPTIONS, MEDIA_A, MEDIA_B, card, settle

A = "CA-09-0C-05"
B = "8E-12-13-05"
C = "11-22-33-44"
MEDIA_C = {"media_content_id": "FV:2/48", "media_content_type": "favorite_item_id"}
PHONE = "mobile_app_iphone_test"
LED_SERVICE = "nfc_musikbox_set_playback_state"


@pytest.fixture
def player(hass: HomeAssistant) -> FakeSonos:
    return FakeSonos(hass, PLAYER, features=ALL_FEATURES).register()


@pytest.fixture
def pushes(hass: HomeAssistant) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _notify(call: ServiceCall) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("notify", PHONE, _notify)
    return calls


@pytest.fixture
def led_calls(hass: HomeAssistant) -> list[str]:
    calls: list[str] = []

    async def _led(call: ServiceCall) -> None:
        calls.append(call.data["player_state"])

    hass.services.async_register("esphome", LED_SERVICE, _led)
    return calls


class Clock:
    """Verschiebt die Uhr der Limits/Mitteilungen; die Event-Loop läuft normal weiter."""

    def __init__(self) -> None:
        self.offset = timedelta()

    def utcnow(self) -> datetime:
        return dt_util.utcnow() + self.offset

    def now(self) -> datetime:
        return dt_util.now() + self.offset

    async def tick(self, hass: HomeAssistant, **delta: float) -> None:
        self.offset += timedelta(**delta)
        async_fire_time_changed(hass, dt_util.utcnow() + self.offset)
        await settle(hass)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    fake = SimpleNamespace(utcnow=clock.utcnow, now=clock.now)
    monkeypatch.setattr(limits_module, "dt_util", fake)
    monkeypatch.setattr(parents, "dt_util", fake)
    return clock


@pytest.fixture(autouse=True)
def fast_fade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scanner, "SLEEP_FADE_SECONDS", 0.05)


async def setup(hass: HomeAssistant, **parental: Any) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = make_entry(make_reader(hass), options=FAST_OPTIONS)
    hass.states.async_set(CARD_SENSOR, "none")
    hass.states.async_set(PLAY_EVENT, "unknown", {"event_type": None})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    store = entry.runtime_data.store
    await store.async_set_card(Card(A, "Hörspiel A", MEDIA_A, limited=True))
    await store.async_set_card(Card(B, "Einschlafmusik", MEDIA_B, mode="simple"))
    await store.async_set_card(Card(C, "Hörspiel C", MEDIA_C, limited=True))
    await store.async_set_parental(**{"notify": [PHONE], **parental})
    await settle(hass)
    return entry


def requests(pushes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in pushes if "actions" in p.get("data", {})]


def action_titles(push: dict[str, Any]) -> list[str]:
    return [a["title"] for a in push["data"]["actions"]]


async def answer(hass: HomeAssistant, push: dict[str, Any], title: str) -> None:
    action = next(a["action"] for a in push["data"]["actions"] if a["title"] == title)
    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
    await settle(hass)


# ---------- Anzahl ----------


async def test_count_limit_blocks_new_card_and_asks_parents(
    hass: HomeAssistant, player: FakeSonos, pushes: list[dict[str, Any]], led_calls: list[str]
) -> None:
    entry = await setup(hass, daily_count=1)
    limits = entry.runtime_data.limits
    await card(hass, A)
    assert limits.count == 1

    # Dieselbe Karte nochmal zählt nicht neu
    await card(hass, "none")
    await card(hass, A)
    assert limits.count == 1

    # Karte ohne Limit läuft immer
    await card(hass, "none")
    await card(hass, B)
    assert player.services("media_player.play_media")[-1]["media_content_id"] == "FV:2/47"

    await card(hass, "none")
    player.calls.clear()
    led_calls.clear()
    await card(hass, C)
    assert "media_player.play_media" not in player.service_names
    assert led_calls == ["locked"]
    [push] = requests(pushes)
    assert "Hörspiel C" in push["message"]
    assert "1 von 1" in push["message"]
    assert action_titles(push) == ["Erlauben", "Ablehnen"]

    await answer(hass, push, "Erlauben")
    assert player.services("media_player.play_media")[-1]["media_content_id"] == "FV:2/48"
    assert limits.count == 2
    assert limits.usage.extra_count == 1
    # Die Anfrage verschwindet auf allen Geräten
    assert pushes[-1] == {"message": "clear_notification", "data": {"tag": push["data"]["tag"]}}


async def test_deny_keeps_card_blocked(
    hass: HomeAssistant, player: FakeSonos, pushes: list[dict[str, Any]]
) -> None:
    await setup(hass, daily_count=1)
    await card(hass, A)
    await card(hass, "none")
    await card(hass, C)
    player.calls.clear()
    await answer(hass, requests(pushes)[0], "Ablehnen")
    assert player.service_names == []


async def test_request_throttled_but_play_button_asks_again(
    hass: HomeAssistant,
    player: FakeSonos,
    pushes: list[dict[str, Any]],
    clock: Clock,
) -> None:
    await setup(hass, daily_count=1)
    await card(hass, A)
    await card(hass, "none")
    await card(hass, C)
    await press(hass, PLAY_EVENT, "kurz")
    assert len(requests(pushes)) == 1
    await clock.tick(hass, seconds=61)
    await press(hass, PLAY_EVENT, "kurz")
    assert len(requests(pushes)) == 2


async def test_reset_then_play_button_starts_blocked_card(
    hass: HomeAssistant, player: FakeSonos, pushes: list[dict[str, Any]]
) -> None:
    entry = await setup(hass, daily_count=1)
    await card(hass, A)
    await card(hass, "none")
    await card(hass, C)
    player.calls.clear()

    await hass.services.async_call(DOMAIN, "reset_limits", blocking=True)
    assert entry.runtime_data.limits.count == 0
    await press(hass, PLAY_EVENT, "kurz")
    assert player.services("media_player.play_media")[-1]["media_content_id"] == "FV:2/48"
    assert entry.runtime_data.limits.count == 1


async def test_parental_off_ignores_limits(
    hass: HomeAssistant, player: FakeSonos, pushes: list[dict[str, Any]]
) -> None:
    entry = await setup(hass, daily_count=1)
    await entry.runtime_data.store.async_set_reader_settings(
        next(iter(entry.subentries)), parental=False
    )
    await card(hass, A)
    await card(hass, "none")
    await card(hass, C)
    assert len(player.services("media_player.play_media")) == 2
    assert entry.runtime_data.limits.count == 0
    assert requests(pushes) == []


async def test_without_targets_no_notification(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, daily_count=1, notify=["mobile_app_gibtsnicht"])
    await card(hass, A)
    await card(hass, "none")
    await card(hass, C)  # gesperrt, Senden an unbekannten Dienst darf nicht scheitern
    assert entry.runtime_data.notifier.targets == []


# ---------- Hörzeit ----------


async def test_listening_time_counts_only_while_playing(
    hass: HomeAssistant, player: FakeSonos, clock: Clock
) -> None:
    entry = await setup(hass, daily_minutes=60)
    limits = entry.runtime_data.limits
    await card(hass, A)
    player.set_playing(q=1, pos=0)
    await settle(hass)
    await clock.tick(hass, minutes=3)
    player.set_paused(q=1, pos=180)
    await settle(hass)
    await clock.tick(hass, minutes=10)  # pausiert: zählt nicht
    assert round(limits.used_seconds()) == 180

    # Musik ohne Limit zählt nicht
    await card(hass, "none")
    await card(hass, B)
    player.set_playing(q=1, pos=0)
    await clock.tick(hass, minutes=5)
    assert round(limits.used_seconds()) == 180


async def test_time_limit_fades_stops_and_extends(
    hass: HomeAssistant,
    player: FakeSonos,
    pushes: list[dict[str, Any]],
    clock: Clock,
) -> None:
    entry = await setup(hass, daily_minutes=10)
    await card(hass, A)
    player.set_playing(q=2, pos=100)
    await settle(hass)
    player.calls.clear()

    await clock.tick(hass, minutes=10, seconds=1)
    await settle(hass)
    assert "media_player.media_pause" in player.service_names
    assert entry.runtime_data.store.positions[A].q == 2
    [push] = requests(pushes)
    assert "aufgebraucht" in push["message"]
    assert action_titles(push) == ["+15 min", "+30 min", "Ablehnen"]

    # Andere Karte mit Limit bleibt auch gesperrt
    await card(hass, "none")
    player.calls.clear()
    await card(hass, C)
    assert "media_player.play_media" not in player.service_names

    # +15 min: die wartende Karte startet
    await clock.tick(hass, seconds=61)
    await press(hass, PLAY_EVENT, "kurz")
    push = requests(pushes)[-1]
    await answer(hass, push, "+15 min")
    assert player.services("media_player.play_media")[-1]["media_content_id"] == "FV:2/48"
    assert entry.runtime_data.limits.usage.extra_minutes == 15
    remaining = entry.runtime_data.limits.remaining_seconds()
    assert remaining is not None
    assert 14 * 60 < remaining <= 15 * 60


async def test_time_limit_shared_between_cards(
    hass: HomeAssistant, player: FakeSonos, clock: Clock
) -> None:
    entry = await setup(hass, daily_minutes=10)
    await card(hass, A)
    player.set_playing(q=1, pos=0)
    await settle(hass)
    await clock.tick(hass, minutes=6)
    await card(hass, "none")
    await card(hass, C)
    player.set_playing(q=1, pos=0)
    await settle(hass)
    player.calls.clear()
    await clock.tick(hass, minutes=4, seconds=1)
    await settle(hass)
    assert "media_player.media_pause" in player.service_names
    assert entry.runtime_data.limits.remaining_seconds() == 0


async def test_new_day_resets_usage(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, daily_count=1)
    store = entry.runtime_data.store
    store.set_usage(DailyUsage(day="2000-01-01", cards=[A], seconds=500, extra_count=2))
    limits = entry.runtime_data.limits
    assert limits.count == 0
    assert limits.used_seconds() == 0
    assert store.usage.day == dt_util.now().date().isoformat()


async def test_extend_service(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, daily_count=2, daily_minutes=30)
    await hass.services.async_call(
        DOMAIN, "extend_limits", {"count": 1, "minutes": 20}, blocking=True
    )
    limits = entry.runtime_data.limits
    assert limits.allowed_count == 3
    assert limits.allowed_seconds == 50 * 60


# ---------- Live-Aktivität ----------


async def test_live_activity_countdown_and_end(
    hass: HomeAssistant,
    player: FakeSonos,
    pushes: list[dict[str, Any]],
    clock: Clock,
) -> None:
    await setup(hass, daily_minutes=30, live_activity=True)
    await card(hass, A)
    player.set_playing(q=2, pos=0)
    hass.states.async_set(
        PLAYER, "playing", {**hass.states.get(PLAYER).attributes, "queue_size": 5}
    )
    await settle(hass)
    await clock.tick(hass, seconds=4)
    await settle(hass)
    live = [p for p in pushes if p.get("data", {}).get("live_update")]
    assert live, pushes
    first = live[-1]
    assert first["title"] == "Hörspiel A"
    assert first["message"].startswith("Kapitel 2 von 5")
    data = first["data"]
    assert data["progress_max"] == 30 * 60
    assert data["progress_bar_direction"] == "decreasing"
    assert data["chronometer"] is True
    assert "silent" not in data

    await card(hass, "none")
    await settle(hass)
    assert pushes[-1] == {"message": "clear_notification", "data": {"tag": data["tag"]}}


async def test_live_activity_chapter_bar_without_limits(
    hass: HomeAssistant,
    player: FakeSonos,
    pushes: list[dict[str, Any]],
    clock: Clock,
) -> None:
    await setup(hass, live_activity=True)
    await card(hass, A)
    player.set_playing(q=3, pos=0)
    hass.states.async_set(
        PLAYER, "playing", {**hass.states.get(PLAYER).attributes, "queue_size": 4}
    )
    await settle(hass)
    await clock.tick(hass, seconds=4)
    await settle(hass)
    data = next(p for p in pushes if p.get("data", {}).get("live_update"))["data"]
    assert (data["progress"], data["progress_max"]) == (3, 4)
    assert "chronometer" not in data


async def test_live_activity_off_by_default(
    hass: HomeAssistant,
    player: FakeSonos,
    pushes: list[dict[str, Any]],
    clock: Clock,
) -> None:
    await setup(hass, daily_minutes=30)
    await card(hass, A)
    player.set_playing(q=1, pos=0)
    await clock.tick(hass, seconds=4)
    await settle(hass)
    assert pushes == []


# ---------- Entitäten und Websocket ----------


async def test_usage_entities(hass: HomeAssistant, player: FakeSonos, clock: Clock) -> None:
    entry = await setup(hass, daily_count=3, daily_minutes=45)
    ent_reg = er.async_get(hass)
    count_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_count_today")
    time_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_time_today")
    reset_id = ent_reg.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_reset_limits")
    assert count_id and time_id and reset_id

    await card(hass, A)
    player.set_playing(q=1, pos=0)
    await settle(hass)
    state = hass.states.get(count_id)
    assert state.state == "1"
    assert state.attributes["limit"] == 3
    assert state.attributes["cards"] == ["Hörspiel A"]

    await clock.tick(hass, minutes=2)
    await hass.async_block_till_done()
    assert float(hass.states.get(time_id).state) == pytest.approx(2.0, abs=0.1)

    await hass.services.async_call("button", "press", {"entity_id": reset_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(count_id).state == "0"


async def test_ws_parental_settings(
    hass: HomeAssistant,
    player: FakeSonos,
    pushes: list[dict[str, Any]],
    hass_ws_client: WebSocketGenerator,
) -> None:
    entry = await setup(hass)
    rid = next(iter(entry.subentries))
    ws = await hass_ws_client(hass)

    await ws.send_json_auto_id(
        {
            "type": f"{DOMAIN}/parental/update",
            "daily_count": 2,
            "daily_minutes": 40,
            "notify": [PHONE],
            "live_activity": True,
        }
    )
    assert (await ws.receive_json())["success"]
    await ws.send_json_auto_id({"type": f"{DOMAIN}/parental/update", "notify": ["mobile_app_x"]})
    assert (await ws.receive_json())["error"]["code"] == "unknown_service"
    await ws.send_json_auto_id({"type": f"{DOMAIN}/reader/update", "reader": rid, "max_volume": 60})
    assert (await ws.receive_json())["success"]
    await ws.send_json_auto_id({"type": f"{DOMAIN}/limits/extend", "minutes": 10})
    assert (await ws.receive_json())["success"]

    await ws.send_json_auto_id({"type": f"{DOMAIN}/subscribe"})
    await ws.receive_json()
    event = (await ws.receive_json())["event"]
    assert event["parental"] == {
        "daily_count": 2,
        "daily_minutes": 40,
        "notify": [PHONE],
        "live_activity": True,
    }
    assert event["usage"]["allowed_seconds"] == 50 * 60
    assert event["notify_services"] == [PHONE]
    assert event["readers"][0]["max_volume"] == 60

    await ws.send_json_auto_id({"type": f"{DOMAIN}/limits/reset"})
    while (msg := await ws.receive_json())["type"] != "result":
        pass
    assert msg["success"]
    assert entry.runtime_data.limits.usage.extra_minutes == 0


async def test_ws_card_save_limited(
    hass: HomeAssistant, player: FakeSonos, hass_ws_client: WebSocketGenerator
) -> None:
    entry = await setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {
            "type": f"{DOMAIN}/card/save",
            "tag_id": "aa-bb",
            "name": "Neu",
            "media": MEDIA_A,
            "limited": True,
        }
    )
    result = await ws.receive_json()
    assert result["success"]
    assert result["result"]["limited"] is True
    assert entry.runtime_data.store.cards["AA-BB"].limited is True

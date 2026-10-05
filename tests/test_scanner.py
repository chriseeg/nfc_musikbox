"""Zustandsmaschine: Tonie und Einfach mit simuliertem Sonos."""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nfc_musikbox.const import DEFAULT_OPTIONS
from custom_components.nfc_musikbox.store import Card, Position

from .conftest import CARD_SENSOR, PLAYER, make_entry, make_reader
from .fake_player import FakeSonos

A = "CA-09-0C-05"
B = "8E-12-13-05"
MEDIA_A = {"media_content_id": "FV:2/46", "media_content_type": "favorite_item_id"}
MEDIA_B = {"media_content_id": "FV:2/47", "media_content_type": "favorite_item_id"}

FAST_OPTIONS = {
    **DEFAULT_OPTIONS,
    "card_change_delay": 0.01,
    "restore_settle": 0.0,
    "restore_start_timeout": 0.3,
    "queue_jump_timeout": 0.3,
    "seek_retry_delay": 0.01,
}


@pytest.fixture
def player(hass: HomeAssistant) -> FakeSonos:
    return FakeSonos(hass, PLAYER).register()


async def setup(hass: HomeAssistant, *cards: Card) -> MockConfigEntry:
    entry = make_entry(make_reader(hass), options=FAST_OPTIONS)
    hass.states.async_set(CARD_SENSOR, "none")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    for card in cards:
        await entry.runtime_data.store.async_set_card(card)
    return entry


async def card(hass: HomeAssistant, value: str) -> None:
    hass.states.async_set(CARD_SENSOR, value)
    await settle(hass)


async def settle(hass: HomeAssistant) -> None:
    """Laufende Abläufe der Controller abwarten."""
    await hass.async_block_till_done()
    for _ in range(50):
        await asyncio.sleep(0.02)
        await hass.async_block_till_done()
        tasks = [t for t in asyncio.all_tasks() if t.get_name().startswith("nfc_musikbox")]
        if all(t.done() for t in tasks):
            return


def tonie(tag: str = A, media: dict | None = None, **kwargs: object) -> Card:
    return Card(tag_id=tag, name=f"Karte {tag}", media=media or MEDIA_A, **kwargs)  # type: ignore[arg-type]


async def test_place_without_memory_starts_media(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass, tonie())
    await card(hass, A)
    assert player.service_names == ["media_player.play_media"]
    assert player.services("media_player.play_media") == [MEDIA_A]


async def test_remove_saves_position_with_rewind_and_pauses(
    hass: HomeAssistant, player: FakeSonos
) -> None:
    entry = await setup(hass, tonie())
    await card(hass, A)
    player.calls.clear()
    player.set_playing(q=2, pos=100, seconds_ago=10)

    await card(hass, "none")

    assert player.service_names == ["media_player.media_pause"]
    pos = entry.runtime_data.store.positions[A]
    assert pos.q == 2
    assert pos.t == "Kapitel 2"
    assert pos.p == 108  # 100 + 10 s gespielt - 2 s Rücksprung


async def test_remove_while_idle_clears_position(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=1, p=50, t="Kapitel 1"))
    await card(hass, A)
    player.calls.clear()
    player.state = "idle"
    player.publish()

    await card(hass, "none")
    assert A not in entry.runtime_data.store.positions
    assert player.service_names == []


async def test_place_restores_queue_and_position(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=2, p=4210, t="Kapitel 2"))

    await card(hass, A)

    assert player.service_names == [
        "media_player.play_media",
        "sonos.play_queue",
        "media_player.media_seek",
    ]
    assert player.services("sonos.play_queue") == [{"queue_position": 1}]
    assert player.services("media_player.media_seek") == [{"seek_position": 4210}]
    assert player.q == 2


async def test_place_restore_first_track_only_seeks(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=1, p=300, t="Kapitel 1"))
    await card(hass, A)
    assert player.service_names == ["media_player.play_media", "media_player.media_seek"]


async def test_small_position_is_not_restored(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=1, p=4, t="Kapitel 1"))
    await card(hass, A)
    assert player.service_names == ["media_player.play_media"]


async def test_place_paused_same_spot_just_plays(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=2, p=100, t="Kapitel 2"))
    player.set_paused(q=2, pos=100)

    await card(hass, A)
    assert player.service_names == ["media_player.media_play"]


async def test_place_paused_other_title_restarts(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=2, p=100, t="Kapitel 2"))
    player.set_paused(q=3, pos=100)

    await card(hass, A)
    assert player.service_names[0] == "media_player.play_media"


async def test_seek_retries_until_position_reached(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=1, p=600, t="Kapitel 1"))
    player.ignore_seeks = 1

    await card(hass, A)
    assert len(player.services("media_player.media_seek")) == 2


async def test_seek_gives_up_after_attempts(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=1, p=600, t="Kapitel 1"))
    player.ignore_seeks = 99

    await card(hass, A)
    assert len(player.services("media_player.media_seek")) == 3


async def test_restore_aborts_if_playback_never_starts(
    hass: HomeAssistant, player: FakeSonos, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=2, p=100, t="Kapitel 2"))
    player.start_delay = None

    await card(hass, A)
    assert player.service_names == ["media_player.play_media"]
    assert "Fortsetzen abgebrochen" in caplog.text


async def test_card_change_saves_old_without_pause_and_starts_new(
    hass: HomeAssistant, player: FakeSonos
) -> None:
    entry = await setup(hass, tonie(), tonie(B, MEDIA_B))
    await card(hass, A)
    player.calls.clear()
    player.set_playing(q=3, pos=42)

    await card(hass, B)

    assert player.service_names == ["media_player.play_media"]
    assert player.services("media_player.play_media") == [MEDIA_B]
    assert entry.runtime_data.store.positions[A] == Position(q=3, p=40, t="Kapitel 3")


async def test_remove_during_restore_keeps_memory(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    memory = Position(q=2, p=4210, t="Kapitel 2")
    entry.runtime_data.store.set_position(A, memory)
    player.start_delay = None  # Fortsetzen hängt beim Warten auf "playing"
    entry.runtime_data.options["restore_start_timeout"] = 5

    hass.states.async_set(CARD_SENSOR, A)
    await hass.async_block_till_done()
    await asyncio.sleep(0.05)
    player.set_playing(q=1, pos=3)  # Player spielt schon, Queue-Sprung steht aus
    player.start_delay = 0
    hass.states.async_set(CARD_SENSOR, "none")
    await settle(hass)

    assert entry.runtime_data.store.positions[A] == memory
    assert "sonos.play_queue" not in player.service_names
    assert player.service_names[-1] == "media_player.media_pause"


@pytest.mark.parametrize(
    ("old", "new"),
    [("unavailable", A), ("unknown", A), (A, "unavailable"), (A, "unknown")],
)
async def test_unavailable_transitions_ignored(
    hass: HomeAssistant, player: FakeSonos, old: str, new: str
) -> None:
    await setup(hass, tonie())
    hass.states.async_set(CARD_SENSOR, old)
    await settle(hass)
    player.calls.clear()
    await card(hass, new)
    assert player.service_names == []


async def test_simple_mode(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie(mode="simple"))
    entry.runtime_data.store.set_position(A, Position(q=2, p=100, t="Kapitel 2"))

    await card(hass, A)
    assert player.service_names == ["media_player.play_media"]

    player.calls.clear()
    await card(hass, "none")
    assert player.service_names == []


async def test_disabled_unknown_and_foreign_cards_do_nothing(
    hass: HomeAssistant, player: FakeSonos
) -> None:
    await setup(
        hass,
        tonie(enabled=False),
        tonie(B, MEDIA_B, readers=["anderes_lesegeraet"]),
    )
    for tag in (A, "none", B, "none", "04-92-E3-92-F3-67-80", "none"):
        await card(hass, tag)
    assert player.service_names == []


async def test_card_without_media(hass: HomeAssistant, player: FakeSonos) -> None:
    await setup(hass, Card(tag_id=A, name="leer"))
    await card(hass, A)
    assert player.service_names == []


async def test_generic_player_starts_from_beginning(hass: HomeAssistant) -> None:
    player = FakeSonos(hass, PLAYER).register(platform="cast")
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=2, p=4210, t="Kapitel 2"))

    await card(hass, A)
    assert player.service_names == ["media_player.play_media"]


async def test_unload_cancels_running_restore(hass: HomeAssistant, player: FakeSonos) -> None:
    entry = await setup(hass, tonie())
    entry.runtime_data.store.set_position(A, Position(q=2, p=4210, t="Kapitel 2"))
    player.start_delay = None
    entry.runtime_data.options["restore_start_timeout"] = 30

    hass.states.async_set(CARD_SENSOR, A)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    tasks = [t for t in asyncio.all_tasks() if t.get_name().startswith("nfc_musikbox")]
    assert all(t.done() for t in tasks)

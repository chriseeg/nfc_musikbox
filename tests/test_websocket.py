"""Websocket-API, Entitäten pro Karte und Panel."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.nfc_musikbox.const import DOMAIN, card_device_identifier
from custom_components.nfc_musikbox.store import Position

from .conftest import CARD_SENSOR, PLAYER, make_entry, make_reader
from .fake_player import FakeSonos
from .test_scanner import FAST_OPTIONS, settle

A = "CA-09-0C-05"
MEDIA = {
    "entity_id": PLAYER,
    "media_content_id": "FV:2/46",
    "media_content_type": "favorite_item_id",
    "metadata": {"title": "Hörspiel Puderzucker", "thumbnail": "/api/media_player_proxy/x"},
}


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = make_entry(make_reader(hass), options=FAST_OPTIONS)
    hass.states.async_set(CARD_SENSOR, "none")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _save(ws: Any, **kwargs: Any) -> dict[str, Any]:
    await ws.send_json_auto_id(
        {
            "type": f"{DOMAIN}/card/save",
            "tag_id": A,
            "name": "Puderzucker",
            "media": MEDIA,
            **kwargs,
        }
    )
    return await ws.receive_json()


async def test_subscribe_and_save(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": f"{DOMAIN}/subscribe"})
    assert (await ws.receive_json())["success"]
    first = (await ws.receive_json())["event"]
    assert first["loaded"] is True
    assert first["cards"] == []
    (reader,) = first["readers"]
    assert reader["ready"] is True
    assert reader["online"] is True

    # Karte gescannt, noch ohne Zuordnung -> erscheint unter "seen"
    hass.states.async_set(CARD_SENSOR, A)
    await settle(hass)
    event = (await ws.receive_json())["event"]
    assert [s["tag_id"] for s in event["seen"]] == [A]

    await ws.send_json_auto_id(
        {
            "type": f"{DOMAIN}/card/save",
            "tag_id": "ca-09-0c-05",
            "name": "Puderzucker",
            "media": MEDIA,
            "readers": [reader["id"]],
        }
    )
    # Reihenfolge: Event (Store-Änderung) und Ergebnis
    msgs = [await ws.receive_json(), await ws.receive_json()]
    result = next(m for m in msgs if m["type"] == "result")
    assert result["success"]
    assert result["result"]["media"]["metadata"]["title"] == "Hörspiel Puderzucker"
    event = next(m for m in msgs if m["type"] == "event")["event"]
    assert event["cards"][0]["tag_id"] == A
    assert [s["tag_id"] for s in event["seen"]] == [A]  # Scan-Zeit bleibt für die Anzeige


async def test_save_unknown_reader(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    ws = await hass_ws_client(hass)
    msg = await _save(ws, readers=["gibtsnicht"])
    assert not msg["success"]
    assert msg["error"]["code"] == "unknown_reader"


async def test_position_reset_delete_forget(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    store = entry.runtime_data.store
    ws = await hass_ws_client(hass)
    assert (await _save(ws))["success"]
    store.set_position(A, Position(q=2, p=4210, t="Kapitel 2"))

    await ws.send_json_auto_id({"type": f"{DOMAIN}/position/reset", "tag_id": A})
    assert (await ws.receive_json())["success"]
    assert A not in store.positions

    store.mark_seen("04-92-E3", None)
    await ws.send_json_auto_id({"type": f"{DOMAIN}/seen/delete", "tag_id": "04-92-E3"})
    assert (await ws.receive_json())["success"]
    assert "04-92-E3" not in store.seen

    await ws.send_json_auto_id({"type": f"{DOMAIN}/card/delete", "tag_id": A})
    assert (await ws.receive_json())["success"]
    assert A not in store.cards


async def test_play_card(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    player = FakeSonos(hass, PLAYER).register()
    ws = await hass_ws_client(hass)
    assert (await _save(ws))["success"]
    entry.runtime_data.store.set_position(A, Position(q=2, p=4210, t="Kapitel 2"))

    await ws.send_json_auto_id({"type": f"{DOMAIN}/card/play", "tag_id": A})
    msg = await ws.receive_json()
    assert msg["success"]
    await settle(hass)
    assert player.service_names == ["media_player.play_media"]

    player.calls.clear()
    await ws.send_json_auto_id({"type": f"{DOMAIN}/card/play", "tag_id": A, "resume": True})
    assert (await ws.receive_json())["success"]
    await settle(hass)
    assert player.service_names == [
        "media_player.play_media",
        "sonos.play_queue",
        "media_player.media_seek",
    ]

    await ws.send_json_auto_id({"type": f"{DOMAIN}/card/play", "tag_id": "UNBEKANNT"})
    assert not (await ws.receive_json())["success"]


async def test_non_admin_rejected(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json_auto_id({"type": f"{DOMAIN}/subscribe"})
    msg = await ws.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "unauthorized"


async def test_card_entities_follow_store(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    ws = await hass_ws_client(hass)
    assert (await _save(ws))["success"]
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    switch_id = ent_reg.async_get_entity_id("switch", DOMAIN, f"card_{A}_enabled")
    select_id = ent_reg.async_get_entity_id("select", DOMAIN, f"card_{A}_mode")
    sensor_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"card_{A}_position")
    assert switch_id and select_id and sensor_id
    assert hass.states.get(switch_id).state == "on"
    assert hass.states.get(select_id).state == "tonie"
    assert hass.states.get(sensor_id).state == "unknown"

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
    assert entry.runtime_data.store.cards[A].enabled is False
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select_id, "option": "simple"}, blocking=True
    )
    assert entry.runtime_data.store.cards[A].mode == "simple"

    entry.runtime_data.store.set_position(A, Position(q=2, p=4210, t="Kapitel 2"))
    await hass.async_block_till_done()
    state = hass.states.get(sensor_id)
    assert state.state == "Titel 2 · 1:10:10 · Kapitel 2"
    assert state.attributes["seconds"] == 4210

    # Umbenennen ändert den Gerätenamen
    assert (await _save(ws, name="Neu"))["success"]
    await hass.async_block_till_done()
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device_by_identifier(
        card_device_identifier(A), config_entry_id=entry.entry_id
    )
    assert device is not None and device.name == "Neu"

    # Löschen entfernt Gerät und Entitäten
    await ws.send_json_auto_id({"type": f"{DOMAIN}/card/delete", "tag_id": A})
    assert (await ws.receive_json())["success"]
    await hass.async_block_till_done()
    assert dev_reg.async_get(device.id) is None
    assert ent_reg.async_get(switch_id) is None
    assert hass.states.get(switch_id) is None


async def test_entities_restored_after_reload(
    hass: HomeAssistant, entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    ws = await hass_ws_client(hass)
    assert (await _save(ws))["success"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    switch_id = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"card_{A}_enabled")
    assert hass.states.get(switch_id).state == "on"


async def test_panel_registered_and_removed(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    panels = hass.data["frontend_panels"]
    assert "nfc-musikbox" in panels
    panel = panels["nfc-musikbox"]
    assert panel.require_admin is True
    assert panel.config["_panel_custom"]["module_url"].startswith(
        "/nfc_musikbox_static/nfc-musikbox-panel.js?v="
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert "nfc-musikbox" not in hass.data["frontend_panels"]


async def test_panel_js_served(
    hass: HomeAssistant, entry: MockConfigEntry, hass_client: Any
) -> None:
    client = await hass_client()
    resp = await client.get("/nfc_musikbox_static/nfc-musikbox-panel.js")
    assert resp.status == 200
    assert "nfc-musikbox-panel" in await resp.text()

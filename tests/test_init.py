"""Einrichtung, Lesegerät-Auflösung, Diagnose-Sensor und Diagnose."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.nfc_musikbox.const import DOMAIN
from custom_components.nfc_musikbox.diagnostics import async_get_config_entry_diagnostics
from custom_components.nfc_musikbox.store import Card

from .conftest import BACK_EVENT, CARD_SENSOR, PLAY_EVENT, PLAYER, make_entry, make_reader


async def test_setup_resolves_reader(hass: HomeAssistant) -> None:
    reader = make_reader(hass)
    entry = make_entry(reader)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED

    (resolved,) = entry.runtime_data.readers.values()
    assert resolved.card_sensor == CARD_SENSOR
    assert resolved.play_event == PLAY_EVENT
    assert resolved.back_event == BACK_EVENT
    assert resolved.player == PLAYER
    assert resolved.led_action == "nfc_musikbox_set_playback_state"

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, resolved.subentry_id), config_entry_id=entry.entry_id
    )
    assert device is not None
    assert device.via_device_id == reader.device_id

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_reader_found_by_original_name(hass: HomeAssistant) -> None:
    """Umbenannte Entity-ID: Fallback über den Originalnamen der Firmware."""
    reader = make_reader(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_update_entity(CARD_SENSOR, new_entity_id="sensor.musikbox_karte")
    entry = make_entry(reader)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    (resolved,) = entry.runtime_data.readers.values()
    assert resolved.card_sensor == "sensor.musikbox_karte"


async def test_current_card_sensor(hass: HomeAssistant) -> None:
    reader = make_reader(hass)
    hass.states.async_set(CARD_SENSOR, "none")
    entry = make_entry(reader)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await entry.runtime_data.store.async_set_card(Card(tag_id="CA-09-0C-05", name="Puderzucker"))

    sensor_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{next(iter(entry.subentries))}_current_card"
    )
    assert sensor_id is not None
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "none"
    assert state.attributes["led_action"] == "esphome.nfc_musikbox_set_playback_state"

    hass.states.async_set(CARD_SENSOR, "CA-09-0C-05")
    await hass.async_block_till_done()
    assert hass.states.get(sensor_id).state == "Puderzucker"

    hass.states.async_set(CARD_SENSOR, "8E-12-13-05")
    await hass.async_block_till_done()
    assert hass.states.get(sensor_id).state == "8E-12-13-05"

    hass.states.async_set(CARD_SENSOR, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(sensor_id).state == "unknown"


async def test_diagnostics(hass: HomeAssistant) -> None:
    reader = make_reader(hass)
    entry = make_entry(reader)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["options"]["skip_back"] == 30.0
    (reader_diag,) = diag["readers"].values()
    assert reader_diag["card_sensor"] == CARD_SENSOR
    assert diag["cards"] == []

"""Deckel mit Reed-Schalter (Firmware v5): Standby im Panel-Zustand."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nfc_musikbox.websocket_api import snapshot

from .conftest import CARD_SENSOR, STANDBY_SENSOR, make_entry, make_reader
from .test_scanner import FAST_OPTIONS


async def _setup(hass: HomeAssistant, *, with_standby: bool) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = make_entry(make_reader(hass, with_standby=with_standby), options=FAST_OPTIONS)
    hass.states.async_set(CARD_SENSOR, "none")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _reader(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    (reader,) = snapshot(hass, entry.runtime_data)["readers"]
    return reader


async def test_standby_found_and_reported(hass: HomeAssistant) -> None:
    entry = await _setup(hass, with_standby=True)
    assert _reader(hass, entry)["standby_sensor"] == STANDBY_SENSOR

    hass.states.async_set(STANDBY_SENSOR, "on")
    assert _reader(hass, entry)["standby"] is True
    hass.states.async_set(STANDBY_SENSOR, "off")
    assert _reader(hass, entry)["standby"] is False
    hass.states.async_set(STANDBY_SENSOR, "unavailable")
    assert _reader(hass, entry)["standby"] is None


async def test_without_reed_switch(hass: HomeAssistant) -> None:
    entry = await _setup(hass, with_standby=False)
    reader = _reader(hass, entry)
    assert reader["standby_sensor"] is None
    assert reader["standby"] is None

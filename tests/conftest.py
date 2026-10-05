"""Gemeinsame Fixtures."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nfc_musikbox.const import (
    CONF_DEVICE_ID,
    CONF_PLAYER,
    DOMAIN,
    SUBENTRY_READER,
)

PLAYER = "media_player.sonos_kinderzimmer"
CARD_SENSOR = "sensor.kinderzimmer_nfc_musikbox_karte_auf_dem_reader"
PLAY_EVENT = "event.kinderzimmer_nfc_musikbox_ereignis_taste_play_pause"
BACK_EVENT = "event.kinderzimmer_nfc_musikbox_ereignis_taste_zuruck"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    yield


@dataclass
class FakeReader:
    """Ein simuliertes ESPHome-Lesegerät in den Registries."""

    device_id: str
    esphome_entry: MockConfigEntry


def make_reader(
    hass: HomeAssistant,
    *,
    node: str = "nfc-musikbox",
    mac: str = "aa:bb:cc:dd:ee:ff",
    with_card_sensor: bool = True,
) -> FakeReader:
    """ESPHome-Config-Entry, Gerät und Entitäten wie bei der echten Firmware anlegen."""
    esphome_entry = MockConfigEntry(
        domain="esphome", data={"device_name": node, "host": "192.0.2.1"}, unique_id=mac
    )
    esphome_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=esphome_entry.entry_id,
        identifiers={("esphome", mac)},
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        name="NFC Musikbox",
    )
    ent_reg = er.async_get(hass)
    prefix = f"kinderzimmer_{node.replace('-', '_')}"
    entities = [
        ("event", "ereignis_taste_play_pause", "Ereignis Taste Play/Pause"),
        ("event", "ereignis_taste_zuruck", "Ereignis Taste Zurück"),
    ]
    if with_card_sensor:
        entities.append(("sensor", "karte_auf_dem_reader", "Karte auf dem Reader"))
    for domain, object_id, name in entities:
        ent_reg.async_get_or_create(
            domain,
            "esphome",
            f"{mac}-{domain}-{object_id}",
            suggested_object_id=f"{prefix}_{object_id}",
            config_entry=esphome_entry,
            device_id=device.id,
            original_name=name,
        )
    return FakeReader(device_id=device.id, esphome_entry=esphome_entry)


def make_entry(
    reader: FakeReader | None = None, player: str = PLAYER, options: dict | None = None
) -> MockConfigEntry:
    subentries: list[ConfigSubentryData] = []
    if reader is not None:
        subentries.append(
            ConfigSubentryData(
                data={CONF_DEVICE_ID: reader.device_id, CONF_PLAYER: player},
                subentry_type=SUBENTRY_READER,
                title="NFC Musikbox",
                unique_id=reader.device_id,
            )
        )
    return MockConfigEntry(
        domain=DOMAIN,
        title="NFC-Musikbox",
        data={},
        options=options or {},
        subentries_data=subentries,
    )

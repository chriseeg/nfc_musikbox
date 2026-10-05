"""NFC-Musikbox: Karte auflegen = Musik, Karte abziehen = Pause."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DEFAULT_OPTIONS, DOMAIN, SUBENTRY_READER
from .reader import ReaderConfig, resolve_reader
from .store import CardStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass(slots=True)
class NfcMusikboxData:
    """Laufzeitdaten des Config Entries."""

    store: CardStore
    options: dict[str, Any]
    readers: dict[str, ReaderConfig] = field(default_factory=dict)


type NfcMusikboxConfigEntry = ConfigEntry[NfcMusikboxData]


def _register_reader_device(
    hass: HomeAssistant, entry: NfcMusikboxConfigEntry, reader: ReaderConfig
) -> None:
    dev_reg = dr.async_get(hass)
    via_device_id = reader.device_id if dev_reg.async_get(reader.device_id) else None
    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=reader.subentry_id,
        identifiers={(DOMAIN, reader.subentry_id)},
        name=reader.title,
        manufacturer="NFC-Musikbox",
        model="Lesegerät",
        via_device_id=via_device_id,
    )


async def async_setup_entry(hass: HomeAssistant, entry: NfcMusikboxConfigEntry) -> bool:
    """Config Entry einrichten."""
    store = CardStore(hass)
    await store.async_load()

    data = NfcMusikboxData(store=store, options={**DEFAULT_OPTIONS, **entry.options})
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_READER:
            continue
        reader = resolve_reader(hass, subentry)
        data.readers[subentry.subentry_id] = reader
        _register_reader_device(hass, entry, reader)
    entry.runtime_data = data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Neue/geänderte Lesegeräte (Subentries) und Optionen erfordern einen Reload
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _LOGGER.debug("Eingerichtet mit %d Lesegerät(en)", len(data.readers))
    return True


async def _async_reload(hass: HomeAssistant, entry: NfcMusikboxConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NfcMusikboxConfigEntry) -> bool:
    """Config Entry entladen."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

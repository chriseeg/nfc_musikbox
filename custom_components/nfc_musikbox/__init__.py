"""NFC-Musikbox: Karte auflegen = Musik, Karte abziehen = Pause."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    SIGNAL_CARD_ADDED,
    SIGNAL_UPDATED,
    SUBENTRY_READER,
    card_device_identifier,
)
from .led import LedSync
from .reader import ReaderConfig, resolve_reader
from .scanner import ReaderController
from .services import async_setup_services
from .store import CardStore, StoreEvent
from .websocket_api import async_register_websocket_api

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

PANEL_URL_PATH = "nfc-musikbox"
PANEL_COMPONENT = "nfc-musikbox-panel"
STATIC_URL = "/nfc_musikbox_static"
FRONTEND_DIR = Path(__file__).parent / "frontend"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class NfcMusikboxData:
    """Laufzeitdaten des Config Entries."""

    store: CardStore
    options: dict[str, Any]
    readers: dict[str, ReaderConfig] = field(default_factory=dict)
    controllers: dict[str, ReaderController] = field(default_factory=dict)
    leds: dict[str, LedSync] = field(default_factory=dict)


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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Dienste, Websocket-API und Dateien des Panels registrieren (einmalig)."""
    async_setup_services(hass)
    async_register_websocket_api(hass)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
    )
    return True


async def _async_register_panel(hass: HomeAssistant) -> None:
    integration = await async_get_integration(hass, DOMAIN)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title="Musikkarten",
        sidebar_icon="mdi:nfc-variant",
        # Version im URL: Browser lädt nach einem Update die neue Datei
        module_url=f"{STATIC_URL}/nfc-musikbox-panel.js?v={integration.version}",
        require_admin=True,
    )


@callback
def _async_handle_store_event(
    hass: HomeAssistant, entry: NfcMusikboxConfigEntry, event: StoreEvent, tag_id: str
) -> None:
    if event is StoreEvent.READER:
        async_dispatcher_send(hass, SIGNAL_UPDATED)
        return
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device_by_identifier(
        card_device_identifier(tag_id), config_entry_id=entry.entry_id
    )
    if event is StoreEvent.CARD_ADDED:
        async_dispatcher_send(hass, SIGNAL_CARD_ADDED, tag_id)
    elif event is StoreEvent.CARD_UPDATED and device is not None:
        card = entry.runtime_data.store.cards[tag_id]
        if device.name != card.name:
            dev_reg.async_update_device(device.id, name=card.name)
    elif event is StoreEvent.CARD_REMOVED and device is not None:
        dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
    async_dispatcher_send(hass, SIGNAL_UPDATED)


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

    for reader in data.readers.values():
        led = LedSync(hass, reader)
        led.async_start()
        data.leds[reader.subentry_id] = led
        controller = ReaderController(hass, reader, store, data.options, on_locked=led.flash_locked)
        controller.async_start()
        data.controllers[reader.subentry_id] = controller
    entry.async_on_unload(
        store.async_add_listener(
            lambda event, tag_id: _async_handle_store_event(hass, entry, event, tag_id)
        )
    )
    await _async_register_panel(hass)
    # Neue/geänderte Lesegeräte (Subentries) und Optionen erfordern einen Reload
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    async_dispatcher_send(hass, SIGNAL_UPDATED)
    _LOGGER.debug("Eingerichtet mit %d Lesegerät(en)", len(data.readers))
    return True


async def _async_reload(hass: HomeAssistant, entry: NfcMusikboxConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NfcMusikboxConfigEntry) -> bool:
    """Config Entry entladen."""
    for led in entry.runtime_data.leds.values():
        led.async_stop()
    for controller in entry.runtime_data.controllers.values():
        await controller.async_stop()
    await entry.runtime_data.store.async_flush()
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    async_dispatcher_send(hass, SIGNAL_UPDATED)
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: NfcMusikboxConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Kartengerät manuell löschen erlauben, wenn die Karte nicht mehr existiert."""
    for domain, ident in device.identifiers:
        if domain == DOMAIN and ident.startswith("card_"):
            return ident.removeprefix("card_") not in entry.runtime_data.store.cards
    return False

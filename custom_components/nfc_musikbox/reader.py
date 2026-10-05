"""Auflösen der Entitäten und der LED-Aktion eines ESPHome-Lesegeräts."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    BACK_EVENT_NAME,
    BACK_EVENT_SUFFIX,
    CARD_SENSOR_NAME,
    CARD_SENSOR_SUFFIX,
    CONF_DEVICE_ID,
    CONF_PLAYER,
    LED_ACTION_SUFFIX,
    PLAY_EVENT_NAME,
    PLAY_EVENT_SUFFIX,
)

_LOGGER = logging.getLogger(__name__)

ESPHOME_DOMAIN = "esphome"


@dataclass(frozen=True, slots=True)
class ReaderConfig:
    """Ein Lesegerät mit seinem Lautsprecher und den aufgelösten Entitäten."""

    subentry_id: str
    title: str
    device_id: str
    player: str
    card_sensor: str | None
    play_event: str | None
    back_event: str | None
    led_action: str | None

    @property
    def is_ready(self) -> bool:
        """Lesegerät ist nutzbar, wenn der Karten-Sensor gefunden wurde."""
        return self.card_sensor is not None


def _find_entity(
    entries: list[er.RegistryEntry], domain: str, suffix: str, name: str
) -> str | None:
    for entry in entries:
        if entry.domain != domain:
            continue
        if entry.entity_id.endswith(f"_{suffix}") or entry.entity_id == f"{domain}.{suffix}":
            return entry.entity_id
    wanted = name.casefold()
    for entry in entries:
        if entry.domain == domain and (entry.original_name or "").casefold() == wanted:
            return entry.entity_id
    return None


def find_card_sensor(hass: HomeAssistant, device_id: str) -> str | None:
    """Sensor "Karte auf dem Reader" eines Geräts finden."""
    entries = er.async_entries_for_device(er.async_get(hass), device_id)
    return _find_entity(entries, "sensor", CARD_SENSOR_SUFFIX, CARD_SENSOR_NAME)


def esphome_node_name(hass: HomeAssistant, device_id: str) -> str | None:
    """ESPHome-Node-Namen (z. B. "nfc-musikbox") des Geräts ermitteln."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.domain == ESPHOME_DOMAIN:
            name = entry.data.get("device_name")
            if isinstance(name, str) and name:
                return name
    return None


def led_action_name(hass: HomeAssistant, device_id: str) -> str | None:
    """Name der ESPHome-Aktion für den LED-Status, ohne Domain."""
    node = esphome_node_name(hass, device_id)
    if node is None:
        return None
    return f"{node.replace('-', '_')}_{LED_ACTION_SUFFIX}"


def device_title(hass: HomeAssistant, device_id: str) -> str:
    """Anzeigename eines Geräts."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return device_id
    return device.name_by_user or device.name or device_id


def resolve_reader(hass: HomeAssistant, subentry: ConfigSubentry) -> ReaderConfig:
    """Subentry in eine ReaderConfig mit aufgelösten Entitäten überführen."""
    device_id: str = subentry.data[CONF_DEVICE_ID]
    entries = er.async_entries_for_device(er.async_get(hass), device_id)
    reader = ReaderConfig(
        subentry_id=subentry.subentry_id,
        title=subentry.title,
        device_id=device_id,
        player=subentry.data[CONF_PLAYER],
        card_sensor=_find_entity(entries, "sensor", CARD_SENSOR_SUFFIX, CARD_SENSOR_NAME),
        play_event=_find_entity(entries, "event", PLAY_EVENT_SUFFIX, PLAY_EVENT_NAME),
        back_event=_find_entity(entries, "event", BACK_EVENT_SUFFIX, BACK_EVENT_NAME),
        led_action=led_action_name(hass, device_id),
    )
    _LOGGER.debug("Lesegerät aufgelöst: %s", reader)
    if reader.card_sensor is None:
        _LOGGER.warning("Lesegerät %s: Sensor 'Karte auf dem Reader' nicht gefunden", reader.title)
    for attr in ("play_event", "back_event", "led_action"):
        if getattr(reader, attr) is None:
            _LOGGER.warning("Lesegerät %s: %s nicht gefunden", reader.title, attr)
    return reader

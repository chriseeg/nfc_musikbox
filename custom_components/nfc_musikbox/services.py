"""Dienste: Karten zuordnen, entfernen, gemerkte Stelle zurücksetzen."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .const import DOMAIN
from .store import CARD_MODES, Card, CardStore

_LOGGER = logging.getLogger(__name__)

SERVICE_ASSIGN_CARD = "assign_card"
SERVICE_REMOVE_CARD = "remove_card"
SERVICE_RESET_POSITION = "reset_position"

ATTR_TAG_ID = "tag_id"
ATTR_NAME = "name"
ATTR_MEDIA = "media"
ATTR_MODE = "mode"
ATTR_ENABLED = "enabled"
ATTR_READERS = "readers"

# Wie der Media-Selector, aber "metadata" (Titel, Cover) bleibt für die Oberfläche erhalten
MEDIA_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): cv.entity_id,
        vol.Required("media_content_id"): cv.string,
        vol.Required("media_content_type"): cv.string,
        vol.Optional("metadata"): dict,
    }
)

ASSIGN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TAG_ID): cv.string,
        vol.Optional(ATTR_NAME): cv.string,
        vol.Required(ATTR_MEDIA): MEDIA_SCHEMA,
        vol.Optional(ATTR_MODE, default="tonie"): vol.In(CARD_MODES),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        vol.Optional(ATTR_READERS, default=list): vol.All(cv.ensure_list, [cv.string]),
    }
)
TAG_SCHEMA = vol.Schema({vol.Required(ATTR_TAG_ID): cv.string})


def _store(hass: HomeAssistant) -> CardStore:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    store: CardStore = entries[0].runtime_data.store
    return store


def _reader_subentries(hass: HomeAssistant, device_ids: list[str]) -> list[str]:
    """Geräte-IDs der Lesegeräte (Gerät dieser Integration) in Subentry-IDs übersetzen."""
    dev_reg = dr.async_get(hass)
    result = []
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        ident = next((i for i in device.identifiers if i[0] == DOMAIN), None) if device else None
        if ident is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_reader",
                translation_placeholders={"device": device_id},
            )
        result.append(ident[1])
    return result


def _normalize_tag(tag_id: str) -> str:
    return tag_id.strip().upper()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def assign_card(call: ServiceCall) -> None:
        store = _store(hass)
        tag_id = _normalize_tag(call.data[ATTR_TAG_ID])
        existing = store.cards.get(tag_id)
        media: dict[str, Any] = dict(call.data[ATTR_MEDIA])
        card = Card(
            tag_id=tag_id,
            name=call.data.get(ATTR_NAME)
            or (existing.name if existing else None)
            or str((media.get("metadata") or {}).get("title") or tag_id),
            media=media,
            mode=call.data[ATTR_MODE],
            enabled=call.data[ATTR_ENABLED],
            readers=_reader_subentries(hass, call.data[ATTR_READERS]),
        )
        await store.async_set_card(card)
        _LOGGER.info("Karte %s (%s) zugeordnet: %s", card.name, tag_id, media)

    async def remove_card(call: ServiceCall) -> None:
        store = _store(hass)
        tag_id = _normalize_tag(call.data[ATTR_TAG_ID])
        if tag_id not in store.cards:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_card",
                translation_placeholders={"tag_id": tag_id},
            )
        await store.async_remove_card(tag_id)

    async def reset_position(call: ServiceCall) -> None:
        store = _store(hass)
        store.set_position(_normalize_tag(call.data[ATTR_TAG_ID]), None)

    hass.services.async_register(DOMAIN, SERVICE_ASSIGN_CARD, assign_card, ASSIGN_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_CARD, remove_card, TAG_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET_POSITION, reset_position, TAG_SCHEMA)

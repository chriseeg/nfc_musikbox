"""Websocket-API für das Panel. Alle Befehle nur für Admins."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import voluptuous as vol

from .const import DOMAIN, SIGNAL_UPDATED
from .players import fmt_position
from .reader import reader_online
from .services import MEDIA_SCHEMA, loaded_data, start_test_playback
from .store import CARD_MODES, REPEAT_MODES, Card

if TYPE_CHECKING:
    from . import NfcMusikboxData

TAG = vol.All(cv.string, lambda v: v.strip().upper())
PERCENT = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    for command in (
        ws_subscribe,
        ws_save_card,
        ws_delete_card,
        ws_reset_position,
        ws_forget_tag,
        ws_play_card,
        ws_update_reader,
    ):
        websocket_api.async_register_command(hass, command)


def _card_dict(data: NfcMusikboxData, card: Card) -> dict[str, Any]:
    position = data.store.positions.get(card.tag_id)
    return {
        **asdict(card),
        "position": ({**asdict(position), "text": fmt_position(position)} if position else None),
    }


def _reader_settings_dict(
    hass: HomeAssistant, data: NfcMusikboxData, reader_id: str
) -> dict[str, Any]:
    settings = data.store.get_reader_settings(reader_id)
    quiet = hass.states.get(settings.quiet_entity) if settings.quiet_entity else None
    return {
        **asdict(settings),
        "quiet_active": bool(settings.parental and quiet is not None and quiet.state == "on"),
    }


def snapshot(hass: HomeAssistant, data: NfcMusikboxData) -> dict[str, Any]:
    """Gesamter Zustand für das Panel."""
    readers = []
    for reader in data.readers.values():
        controller = data.controllers.get(reader.subentry_id)
        card_state = hass.states.get(reader.card_sensor) if reader.card_sensor else None
        player_state = hass.states.get(reader.player)
        readers.append(
            {
                "id": reader.subentry_id,
                "title": reader.title,
                "device_id": reader.device_id,
                "player": reader.player,
                "player_name": (
                    player_state.attributes.get("friendly_name") if player_state else None
                )
                or reader.player,
                "card_sensor": reader.card_sensor,
                "current_tag": card_state.state if card_state else None,
                "ready": reader.is_ready,
                "online": reader_online(hass, reader),
                "supports_restore": bool(controller and controller.player.supports_restore),
                **_reader_settings_dict(hass, data, reader.subentry_id),
            }
        )
    return {
        "readers": readers,
        "cards": [_card_dict(data, card) for card in data.store.cards.values()],
        # Alle gescannten Tags (auch zugeordnete, für "zuletzt gescannt")
        "seen": [{"tag_id": tag_id, **asdict(seen)} for tag_id, seen in data.store.seen.items()],
    }


def _data_or_error(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg_id: int
) -> NfcMusikboxData | None:
    try:
        return loaded_data(hass)
    except HomeAssistantError:
        connection.send_error(msg_id, "not_loaded", "NFC-Musikbox ist nicht eingerichtet")
        return None


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Zustand senden und bei jeder Änderung erneut senden (überlebt Reloads)."""

    @callback
    def _send() -> None:
        try:
            data = loaded_data(hass)
        except HomeAssistantError:
            payload: dict[str, Any] = {"loaded": False, "readers": [], "cards": [], "seen": []}
        else:
            payload = {"loaded": True, **snapshot(hass, data)}
        connection.send_message(websocket_api.event_message(msg["id"], payload))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_UPDATED, _send)
    connection.send_result(msg["id"])
    _send()


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/card/save",
        vol.Required("tag_id"): TAG,
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=100)),
        vol.Required("media"): MEDIA_SCHEMA,
        vol.Optional("mode", default="tonie"): vol.In(CARD_MODES),
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("readers", default=list): [cv.string],
        vol.Optional("shuffle", default=None): vol.Any(None, cv.boolean),
        vol.Optional("repeat", default=None): vol.Any(None, vol.In(REPEAT_MODES)),
        vol.Optional("allow_in_quiet", default=False): cv.boolean,
    }
)
@websocket_api.async_response
async def ws_save_card(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    data = _data_or_error(hass, connection, msg["id"])
    if data is None:
        return
    unknown = [r for r in msg["readers"] if r not in data.readers]
    if unknown:
        connection.send_error(msg["id"], "unknown_reader", f"Unbekanntes Lesegerät: {unknown}")
        return
    card = Card(
        tag_id=msg["tag_id"],
        name=msg["name"].strip(),
        media=dict(msg["media"]),
        mode=msg["mode"],
        enabled=msg["enabled"],
        readers=msg["readers"],
        shuffle=msg["shuffle"],
        repeat=msg["repeat"],
        allow_in_quiet=msg["allow_in_quiet"],
    )
    await data.store.async_set_card(card)
    connection.send_result(msg["id"], _card_dict(data, card))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/card/delete", vol.Required("tag_id"): TAG}
)
@websocket_api.async_response
async def ws_delete_card(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    data = _data_or_error(hass, connection, msg["id"])
    if data is None:
        return
    await data.store.async_remove_card(msg["tag_id"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/position/reset", vol.Required("tag_id"): TAG}
)
@callback
def ws_reset_position(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    data = _data_or_error(hass, connection, msg["id"])
    if data is None:
        return
    data.store.set_position(msg["tag_id"], None)
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/seen/delete", vol.Required("tag_id"): TAG}
)
@callback
def ws_forget_tag(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    data = _data_or_error(hass, connection, msg["id"])
    if data is None:
        return
    data.store.forget_seen(msg["tag_id"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/card/play",
        vol.Required("tag_id"): TAG,
        vol.Optional("reader"): cv.string,
        vol.Optional("resume", default=False): cv.boolean,
    }
)
@callback
def ws_play_card(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        reader_id = start_test_playback(hass, msg["tag_id"], msg.get("reader"), msg["resume"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "play_failed", str(err))
        return
    connection.send_result(msg["id"], {"reader": reader_id})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/reader/update",
        vol.Required("reader"): cv.string,
        vol.Optional("start_volume"): PERCENT,
        vol.Optional("max_volume"): PERCENT,
        vol.Optional("sleep_timer"): vol.All(vol.Coerce(int), vol.Range(min=0, max=180)),
        vol.Optional("parental"): cv.boolean,
        vol.Optional("quiet_entity"): vol.Any(None, "", cv.entity_id),
    }
)
@websocket_api.async_response
async def ws_update_reader(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    data = _data_or_error(hass, connection, msg["id"])
    if data is None:
        return
    if msg["reader"] not in data.readers:
        connection.send_error(msg["id"], "unknown_reader", "Unbekanntes Lesegerät")
        return
    changes = {k: v for k, v in msg.items() if k not in ("id", "type", "reader")}
    quiet = changes.get("quiet_entity")
    if quiet and hass.states.get(quiet) is None:
        connection.send_error(msg["id"], "unknown_entity", f"Entität {quiet} gibt es nicht")
        return
    await data.store.async_set_reader_settings(msg["reader"], **changes)
    connection.send_result(msg["id"])

"""Wiedergabestatus des Lautsprechers an die LED des Lesegeräts senden."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.const import ATTR_DEVICE_ID, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import (
    CoreState,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event

from .const import EVENT_READER_ONLINE
from .reader import ESPHOME_DOMAIN, ReaderConfig, esphome_node_name

_LOGGER = logging.getLogger(__name__)

# Die Firmware kennt nur "playing" und "paused"; Puffern soll wie Spielen aussehen
STATE_MAP = {"buffering": "playing"}


class LedSync:
    """Sendet den Player-Zustand an die ESPHome-Aktion `<node>_set_playback_state`.

    Gesendet wird bei Zustandsänderung des Players (nicht bei Attributänderungen),
    beim HA-Start und wenn sich das Lesegerät neu verbindet.
    """

    def __init__(self, hass: HomeAssistant, reader: ReaderConfig) -> None:
        self.hass = hass
        self.reader = reader
        self._last_sent: str | None = None
        self._lock = asyncio.Lock()
        self._unsub: list[Any] = []
        self._tasks: set[asyncio.Task[None]] = set()

    @callback
    def async_start(self) -> None:
        if self.reader.led_action is None:
            _LOGGER.warning("%s: keine LED-Aktion gefunden, LED bleibt aus", self.reader.title)
            return
        self._unsub.append(
            async_track_state_change_event(self.hass, self.reader.player, self._handle_player)
        )
        self._unsub.append(self.hass.bus.async_listen(EVENT_READER_ONLINE, self._handle_online))
        if self.hass.state is CoreState.running:
            self.schedule(force=True)
        else:
            self._unsub.append(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, lambda _: self.schedule(force=True)
                )
            )

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        for task in self._tasks:
            task.cancel()

    @callback
    def _handle_player(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is not None and new is not None and old.state == new.state:
            return
        self.schedule()

    @callback
    def _handle_online(self, event: Event[dict[str, Any]]) -> None:
        if not self._is_own_reader(event.data):
            return
        _LOGGER.debug("%s: Lesegerät online, sende LED-Status", self.reader.title)
        self.schedule(force=True)

    def _is_own_reader(self, data: dict[str, Any]) -> bool:
        if data.get(ATTR_DEVICE_ID) is not None:
            return bool(data[ATTR_DEVICE_ID] == self.reader.device_id)
        node = esphome_node_name(self.hass, self.reader.device_id)
        return node is not None and data.get("reader") == node

    def current_value(self) -> str:
        state = self.hass.states.get(self.reader.player)
        raw = state.state if state is not None else "unavailable"
        return STATE_MAP.get(raw, raw)

    @callback
    def schedule(self, force: bool = False) -> None:
        # Beim Herunterfahren meldet der Player "unavailable"; das muss nicht mehr raus
        if self.hass.is_stopping:
            return
        task = self.hass.async_create_background_task(
            self._send(force), f"nfc_musikbox {self.reader.title} LED"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, force: bool) -> None:
        action = self.reader.led_action
        if action is None:
            return
        async with self._lock:
            value = self.current_value()
            if not force and value == self._last_sent:
                return
            if not self.hass.services.has_service(ESPHOME_DOMAIN, action):
                # Lesegerät offline; beim Wiederverbinden kommt nfc_reader_online
                _LOGGER.debug("%s: esphome.%s nicht verfügbar", self.reader.title, action)
                self._last_sent = None
                return
            try:
                await self.hass.services.async_call(
                    ESPHOME_DOMAIN, action, {"player_state": value}, blocking=True
                )
            except HomeAssistantError as err:
                _LOGGER.debug("%s: LED-Status nicht gesendet: %s", self.reader.title, err)
                self._last_sent = None
                return
            _LOGGER.debug("%s: LED-Status %s gesendet", self.reader.title, value)
            self._last_sent = value

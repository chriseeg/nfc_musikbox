"""Zustandsmaschine pro Lesegerät: Karte auflegen, entfernen, wechseln."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import logging
from typing import Any

from homeassistant.const import STATE_PAUSED, STATE_PLAYING, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    BUTTON_EVENT_MAX_AGE,
    EVENT_TYPE_LONG,
    EVENT_TYPE_SHORT,
    NO_CARD,
    OPT_CARD_CHANGE_DELAY,
    OPT_REMOVAL_REWIND,
    OPT_SKIP_BACK,
)
from .players import (
    MIN_RESTORE_SECONDS,
    PlayerBackend,
    create_backend,
    current_position,
    fmt_position,
    media_title,
    queue_position,
    snapshot_position,
)
from .reader import ReaderConfig
from .store import TITLE_MAX_LEN, Card, CardStore

_LOGGER = logging.getLogger(__name__)

IGNORED_STATES = {STATE_UNAVAILABLE, STATE_UNKNOWN, ""}


class ReaderController:
    """Reagiert auf den Sensor "Karte auf dem Reader" eines Lesegeräts.

    Pro Lesegerät läuft höchstens ein Ablauf. Ein neues Ereignis bricht den
    laufenden Ablauf ab (wie `mode: restart` im alten Blueprint).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        reader: ReaderConfig,
        store: CardStore,
        options: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.reader = reader
        self.store = store
        self.options = options
        self._task: asyncio.Task[None] | None = None
        # Karte, deren Fortsetzen gerade läuft; ihre gemerkte Stelle ist dann noch gültig
        self._restoring: str | None = None
        self._unsub: list[Any] = []

    @property
    def name(self) -> str:
        return self.reader.title

    @property
    def player(self) -> PlayerBackend:
        """Backend bei jeder Nutzung wählen (Registry-Plattform kann sich ändern)."""
        return create_backend(self.hass, self.reader.player, self.options)

    @callback
    def async_start(self) -> None:
        if self.reader.card_sensor is None:
            return
        self._unsub.append(
            async_track_state_change_event(
                self.hass, self.reader.card_sensor, self._handle_card_sensor
            )
        )
        for entity_id, handler in (
            (self.reader.play_event, self._handle_play_button),
            (self.reader.back_event, self._handle_back_button),
        ):
            if entity_id is not None:
                self._unsub.append(async_track_state_change_event(self.hass, entity_id, handler))
        _LOGGER.debug(
            "%s: höre auf %s, Player %s (%s)",
            self.name,
            self.reader.card_sensor,
            self.reader.player,
            type(self.player).__name__,
        )

    async def async_stop(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        await self._cancel_running()

    @property
    def card_on_reader(self) -> bool:
        state = self.hass.states.get(self.reader.card_sensor or "")
        return state is not None and state.state not in IGNORED_STATES | {NO_CARD}

    async def _cancel_running(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        # asyncio.wait statt try/except: eigener Abbruch darf nicht verschluckt werden
        await asyncio.wait([task])

    @callback
    def _handle_card_sensor(self, event: Event[EventStateChangedData]) -> None:
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if old_state is None or new_state is None:
            return
        old, new = old_state.state, new_state.state
        if old == new:
            return
        # Reconnect oder HA-Start: kein Auslösen
        if old in IGNORED_STATES or new in IGNORED_STATES:
            _LOGGER.debug("%s: ignoriere %r -> %r", self.name, old, new)
            return
        self.handle_change(None if old == NO_CARD else old, None if new == NO_CARD else new)

    @callback
    def handle_change(self, removed: str | None, placed: str | None) -> None:
        """Ereignis verarbeiten; bricht einen laufenden Ablauf ab."""
        _LOGGER.debug("%s: Karte %s -> %s", self.name, removed or "-", placed or "-")
        if placed is not None:
            self.store.mark_seen(placed, self.reader.subentry_id)
        interrupted_restore = self._restoring
        previous = self._task
        self._task = self.hass.async_create_background_task(
            self._run(removed, placed, previous, interrupted_restore),
            f"nfc_musikbox {self.name}",
        )

    async def _run(
        self,
        removed: str | None,
        placed: str | None,
        previous: asyncio.Task[None] | None,
        interrupted_restore: str | None,
    ) -> None:
        if previous is not None and not previous.done():
            _LOGGER.debug("%s: breche laufenden Ablauf ab", self.name)
            previous.cancel()
            await asyncio.wait([previous])
        try:
            removed_card = self._active_card(removed)
            if removed_card is not None:
                await self._on_removed(
                    removed_card,
                    still_playing=placed is not None,
                    skip_save=interrupted_restore == removed_card.tag_id,
                )
            placed_card = self._active_card(placed)
            if placed is not None and placed_card is None:
                _LOGGER.debug("%s: Karte %s ist nicht zugeordnet oder inaktiv", self.name, placed)
            if placed_card is not None:
                if removed is not None:
                    await asyncio.sleep(float(self.options[OPT_CARD_CHANGE_DELAY]))
                await self._on_placed(placed_card)
        except HomeAssistantError as err:
            _LOGGER.error("%s: Fehler bei %s -> %s: %s", self.name, removed, placed, err)

    @callback
    def play_card(self, card: Card, *, resume: bool) -> asyncio.Task[None]:
        """Karte zum Testen abspielen, als läge sie auf; bricht laufende Abläufe ab."""
        previous = self._task
        self._task = self.hass.async_create_background_task(
            self._run_play(card, resume, previous), f"nfc_musikbox {self.name} Test"
        )
        return self._task

    async def _run_play(
        self, card: Card, resume: bool, previous: asyncio.Task[None] | None
    ) -> None:
        if previous is not None and not previous.done():
            previous.cancel()
            await asyncio.wait([previous])
        _LOGGER.info("%s: Test-Wiedergabe %s (fortsetzen: %s)", self.name, card.name, resume)
        try:
            if resume and card.mode == "tonie":
                await self._on_placed(card)
            elif card.media is not None:
                await self.player.async_play_media(card.media)
        except HomeAssistantError as err:
            _LOGGER.error("%s: Test-Wiedergabe fehlgeschlagen: %s", self.name, err)

    def _active_card(self, tag_id: str | None) -> Card | None:
        if tag_id is None:
            return None
        card = self.store.cards.get(tag_id)
        if card is None or not card.enabled or not card.works_on(self.reader.subentry_id):
            return None
        return card

    async def _on_removed(self, card: Card, *, still_playing: bool, skip_save: bool) -> None:
        if card.mode != "tonie":
            return
        state = self.player.state
        if skip_save:
            _LOGGER.info(
                "%s: %s entfernt, während das Fortsetzen lief; gemerkte Stelle bleibt",
                self.name,
                card.name,
            )
        else:
            position = snapshot_position(state, float(self.options[OPT_REMOVAL_REWIND]))
            self.store.set_position(card.tag_id, position)
            if position is None:
                _LOGGER.info(
                    "%s: %s entfernt, Player %s: Stelle gelöscht",
                    self.name,
                    card.name,
                    state.state if state else None,
                )
            else:
                _LOGGER.info(
                    "%s: %s entfernt, gemerkt: %s", self.name, card.name, fmt_position(position)
                )
        # Beim Kartenwechsel nicht pausieren, die neue Karte übernimmt
        if not still_playing and state is not None and state.state == STATE_PLAYING:
            await self.player.async_pause()

    async def _on_placed(self, card: Card) -> None:
        if card.media is None:
            _LOGGER.info("%s: %s hat kein Medium", self.name, card.name)
            return
        if card.mode == "simple":
            _LOGGER.info("%s: %s aufgelegt, starte Medium", self.name, card.name)
            await self.player.async_play_media(card.media)
            return

        memory = self.store.positions.get(card.tag_id)
        state = self.player.state
        if (
            memory is not None
            and memory.t
            and state is not None
            and state.state == STATE_PAUSED
            and media_title(state)[:TITLE_MAX_LEN] == memory.t
            and queue_position(state) == memory.q
        ):
            _LOGGER.info(
                "%s: %s aufgelegt, Player pausiert an gleicher Stelle: weiter", self.name, card.name
            )
            await self.player.async_play()
            return

        _LOGGER.info(
            "%s: %s aufgelegt, starte Medium%s",
            self.name,
            card.name,
            f", fortsetzen bei {fmt_position(memory)}" if memory else "",
        )
        self._restoring = card.tag_id
        try:
            await self.player.async_play_media(card.media)
            if memory is not None and (memory.q > 1 or memory.p > MIN_RESTORE_SECONDS):
                await self.player.async_restore(memory)
        finally:
            self._restoring = None

    # ---------- Tasten ----------

    @staticmethod
    def _button_event_type(event: Event[EventStateChangedData]) -> str | None:
        """event_type eines frischen Tastendrucks, sonst None.

        Der Zustand einer Event-Entität ist der Zeitstempel des letzten Ereignisses.
        Geprüft wird dessen Alter statt des Vorzustands: So zählt auch der erste
        Druck nach dem Start (Vorzustand "unknown"), ein beim Reconnect
        wiederhergestellter alter Zeitstempel aber nicht.
        """
        new_state: State | None = event.data["new_state"]
        old_state: State | None = event.data["old_state"]
        if new_state is None or new_state.state in IGNORED_STATES:
            return None
        if old_state is not None and old_state.state == new_state.state:
            return None
        fired = dt_util.parse_datetime(new_state.state)
        if fired is None:
            return None
        if abs((dt_util.utcnow() - fired).total_seconds()) > BUTTON_EVENT_MAX_AGE:
            return None
        event_type = new_state.attributes.get("event_type")
        return event_type if isinstance(event_type, str) else None

    @callback
    def _handle_play_button(self, event: Event[EventStateChangedData]) -> None:
        if self._button_event_type(event) != EVENT_TYPE_SHORT:
            return
        self._run_button("Play/Pause", self._on_play_button())

    @callback
    def _handle_back_button(self, event: Event[EventStateChangedData]) -> None:
        event_type = self._button_event_type(event)
        if event_type == EVENT_TYPE_SHORT:
            self._run_button("Zurück kurz", self._on_back_short())
        elif event_type == EVENT_TYPE_LONG:
            self._run_button("Zurück lang", self._on_back_long())

    @callback
    def _run_button(self, label: str, action: Coroutine[Any, Any, None]) -> None:
        if not self.card_on_reader:
            _LOGGER.debug("%s: Taste %s ohne Karte ignoriert", self.name, label)
            action.close()
            return
        _LOGGER.debug("%s: Taste %s", self.name, label)
        self.hass.async_create_background_task(
            self._button_task(label, action), f"nfc_musikbox {self.name} Taste"
        )

    async def _button_task(self, label: str, action: Coroutine[Any, Any, None]) -> None:
        # Ein laufendes Fortsetzen würde die Taste gleich wieder überspielen
        if self._restoring is not None:
            _LOGGER.info("%s: Taste %s bricht das Fortsetzen ab", self.name, label)
            await self._cancel_running()
        try:
            await action
        except HomeAssistantError as err:
            _LOGGER.error("%s: Taste %s fehlgeschlagen: %s", self.name, label, err)

    async def _on_play_button(self) -> None:
        await self.player.async_play_pause()

    async def _on_back_short(self) -> None:
        state = self.player.state
        if state is None or state.state not in (STATE_PLAYING, STATE_PAUSED):
            return
        target = max(0, round(current_position(state) - float(self.options[OPT_SKIP_BACK])))
        await self.player.async_seek(target)

    async def _on_back_long(self) -> None:
        await self.player.async_restart()

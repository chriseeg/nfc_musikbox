"""Zustandsmaschine pro Lesegerät: Karte auflegen, entfernen, wechseln."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import logging
from typing import Any

from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import (
    STATE_ON,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
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
from .store import TITLE_MAX_LEN, Card, CardStore, ReaderSettings, StoreEvent

_LOGGER = logging.getLogger(__name__)

IGNORED_STATES = {STATE_UNAVAILABLE, STATE_UNKNOWN, ""}

# Schlaf-Timer: Ausblenden über diese Dauer in so vielen Schritten
SLEEP_FADE_SECONDS = 15.0
SLEEP_FADE_STEPS = 10
# Toleranz, bevor die Maximallautstärke eingreift (Rundung der Player)
VOLUME_TOLERANCE = 0.005


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
        on_locked: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self.reader = reader
        self.store = store
        self.options = options
        self._on_locked = on_locked or (lambda: None)
        self._task: asyncio.Task[None] | None = None
        # Karte, deren Fortsetzen gerade läuft; ihre gemerkte Stelle ist dann noch gültig
        self._restoring: str | None = None
        self._unsub: list[Any] = []
        # Kindersicherung
        self._blocked: str | None = None  # Karte, die wegen Ruhezeit/Schlaf-Timer gesperrt ist
        self._sleep_unsub: CALLBACK_TYPE | None = None
        self._quiet_unsub: CALLBACK_TYPE | None = None
        self._restore_volume: float | None = None  # nach abgebrochenem Ausblenden
        self._fading = False

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
            (self.reader.player, self._handle_player),
        ):
            if entity_id is not None:
                self._unsub.append(async_track_state_change_event(self.hass, entity_id, handler))
        self._unsub.append(self.store.async_add_listener(self._handle_store))
        self._track_quiet()
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
        self._cancel_sleep_timer()
        if self._quiet_unsub is not None:
            self._quiet_unsub()
            self._quiet_unsub = None
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
        self._cancel_sleep_timer()
        blocked_removed = removed is not None and removed == self._blocked
        self._blocked = None
        interrupted_restore = self._restoring
        previous = self._task
        self._task = self.hass.async_create_background_task(
            self._run(removed, placed, previous, interrupted_restore, blocked_removed),
            f"nfc_musikbox {self.name}",
        )

    async def _run(
        self,
        removed: str | None,
        placed: str | None,
        previous: asyncio.Task[None] | None,
        interrupted_restore: str | None,
        blocked_removed: bool = False,
    ) -> None:
        if previous is not None and not previous.done():
            _LOGGER.debug("%s: breche laufenden Ablauf ab", self.name)
            previous.cancel()
            await asyncio.wait([previous])
        try:
            # Eine gesperrte Karte hat nichts gestartet; ihr Entfernen ändert nichts
            removed_card = None if blocked_removed else self._active_card(removed)
            if removed_card is not None:
                await self._on_removed(
                    removed_card,
                    still_playing=placed is not None,
                    skip_save=interrupted_restore == removed_card.tag_id,
                )
            placed_card = self._active_card(placed)
            if placed is not None and placed_card is None:
                _LOGGER.debug("%s: Karte %s ist nicht zugeordnet oder inaktiv", self.name, placed)
            if placed_card is not None and self._quiet_blocks(placed_card):
                _LOGGER.info("%s: %s in der Ruhezeit gesperrt", self.name, placed_card.name)
                self._blocked = placed_card.tag_id
                self._on_locked()
                if removed_card is not None:
                    # Kartenwechsel in der Ruhezeit: die alte Karte nicht weiterlaufen lassen
                    await self._pause_if_playing()
                return
            if placed_card is not None:
                if removed is not None:
                    await asyncio.sleep(float(self.options[OPT_CARD_CHANGE_DELAY]))
                await self._on_placed(placed_card)
                self._start_sleep_timer(placed_card)
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
                await self._apply_start_volume()
                await self._apply_play_mode(card)
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

    @property
    def settings(self) -> ReaderSettings:
        return self.store.get_reader_settings(self.reader.subentry_id)

    def _start_volume_target(self) -> int | None:
        """Lautstärke in % für den Start: Startlautstärke, begrenzt durch die Maximallautstärke."""
        settings = self.settings
        target: int | None = settings.start_volume or None
        if target is None and self._restore_volume is not None:
            target = round(self._restore_volume * 100)
        max_volume = settings.max_volume if settings.parental else 0
        if max_volume:
            current = _volume_level(self.player.state)
            if target is not None:
                target = min(target, max_volume)
            elif current is not None and current > max_volume / 100 + VOLUME_TOLERANCE:
                target = max_volume
        return target

    async def _apply_start_volume(self) -> None:
        volume = self._start_volume_target()
        self._restore_volume = None
        if volume is None:
            return
        player = self.player
        if not player.supports(MediaPlayerEntityFeature.VOLUME_SET):
            _LOGGER.debug("%s: Player kann keine Lautstärke setzen", self.name)
            return
        _LOGGER.debug("%s: Startlautstärke %d %%", self.name, volume)
        try:
            await player.async_set_volume(volume)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: Startlautstärke nicht gesetzt: %s", self.name, err)

    async def _apply_play_mode(self, card: Card) -> None:
        """Shuffle/Repeat vor dem Start setzen.

        Sonos merkt sich Shuffle pro Lautsprecher. Ein Hörspiel muss deshalb immer
        ohne Shuffle starten, sonst stimmt die gemerkte Titelnummer nicht.
        """
        shuffle = False if card.mode == "tonie" else card.shuffle
        repeat = None if card.mode == "tonie" else card.repeat
        player = self.player
        try:
            if shuffle is not None and player.supports(MediaPlayerEntityFeature.SHUFFLE_SET):
                await player.async_set_shuffle(shuffle)
            if repeat is not None and player.supports(MediaPlayerEntityFeature.REPEAT_SET):
                await player.async_set_repeat(repeat)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: Shuffle/Wiederholen nicht gesetzt: %s", self.name, err)

    async def _on_placed(self, card: Card) -> None:
        if card.media is None:
            _LOGGER.info("%s: %s hat kein Medium", self.name, card.name)
            return
        await self._apply_start_volume()
        if card.mode == "simple":
            _LOGGER.info("%s: %s aufgelegt, starte Medium (Musik-Modus)", self.name, card.name)
            await self._apply_play_mode(card)
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
            await self._apply_play_mode(card)
            await self.player.async_play_media(card.media)
            if memory is not None and (memory.q > 1 or memory.p > MIN_RESTORE_SECONDS):
                await self.player.async_restore(memory)
        finally:
            self._restoring = None

    # ---------- Kindersicherung ----------

    def _in_quiet(self) -> bool:
        settings = self.settings
        if not settings.parental or not settings.quiet_entity:
            return False
        state = self.hass.states.get(settings.quiet_entity)
        return state is not None and state.state == STATE_ON

    def _quiet_blocks(self, card: Card) -> bool:
        return self._in_quiet() and not card.allow_in_quiet

    def _card_on_reader(self) -> Card | None:
        state = self.hass.states.get(self.reader.card_sensor or "")
        return self._active_card(state.state if state else None)

    async def _pause_if_playing(self) -> None:
        state = self.player.state
        if state is not None and state.state == STATE_PLAYING:
            await self.player.async_pause()

    @callback
    def _handle_store(self, event: StoreEvent, key: str) -> None:
        if event is StoreEvent.READER and key == self.reader.subentry_id:
            self._track_quiet()

    @callback
    def _track_quiet(self) -> None:
        if self._quiet_unsub is not None:
            self._quiet_unsub()
            self._quiet_unsub = None
        entity_id = self.settings.quiet_entity
        if entity_id:
            self._quiet_unsub = async_track_state_change_event(
                self.hass, entity_id, self._handle_quiet
            )

    @callback
    def _handle_quiet(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]
        if new_state is None or new_state.state != STATE_ON:
            return
        if old_state is not None and old_state.state == STATE_ON:
            return
        card = self._card_on_reader()
        if card is None or not self._quiet_blocks(card):
            return
        _LOGGER.info("%s: Ruhezeit beginnt, %s wird pausiert", self.name, card.name)
        self._run_exclusive(self._stop_for_parental(card), "Ruhezeit")

    @callback
    def _run_exclusive(self, action: Coroutine[Any, Any, None], label: str) -> None:
        """Wie ein Kartenereignis: läuft in der Ablauf-Kette und bricht Laufendes ab."""
        previous = self._task

        async def _wrapped() -> None:
            if previous is not None and not previous.done():
                previous.cancel()
                await asyncio.wait([previous])
            try:
                await action
            except HomeAssistantError as err:
                _LOGGER.error("%s: %s fehlgeschlagen: %s", self.name, label, err)

        self._task = self.hass.async_create_background_task(
            _wrapped(), f"nfc_musikbox {self.name} {label}"
        )

    async def _stop_for_parental(self, card: Card) -> None:
        """Stelle merken (Hörspiel) und pausieren, wie beim Abziehen; Karte sperren."""
        self._cancel_sleep_timer()
        self._blocked = card.tag_id
        if card.mode == "tonie":
            position = snapshot_position(self.player.state)
            if position is not None:
                self.store.set_position(card.tag_id, position)
        await self._pause_if_playing()

    @callback
    def _start_sleep_timer(self, card: Card) -> None:
        self._cancel_sleep_timer()
        minutes = self.settings.sleep_timer if self.settings.parental else 0
        if not minutes:
            return
        _LOGGER.debug("%s: Schlaf-Timer %d min für %s", self.name, minutes, card.name)

        @callback
        def _expired(_now: Any) -> None:
            self._sleep_unsub = None
            if self._card_on_reader() is None:
                return
            _LOGGER.info("%s: Schlaf-Timer abgelaufen, blende %s aus", self.name, card.name)
            self._run_exclusive(self._sleep_fade(card), "Schlaf-Timer")

        self._sleep_unsub = async_call_later(self.hass, minutes * 60, _expired)

    @callback
    def _cancel_sleep_timer(self) -> None:
        if self._sleep_unsub is not None:
            self._sleep_unsub()
            self._sleep_unsub = None

    async def _sleep_fade(self, card: Card) -> None:
        player = self.player
        state = player.state
        if state is None or state.state != STATE_PLAYING:
            self._blocked = card.tag_id
            return
        original = _volume_level(state)
        if original is None or not player.supports(MediaPlayerEntityFeature.VOLUME_SET):
            await self._stop_for_parental(card)
            return
        # Wird das Ausblenden abgebrochen (Karte abgezogen), beim nächsten Start zurück
        self._restore_volume = original
        self._fading = True
        try:
            for step in range(1, SLEEP_FADE_STEPS + 1):
                await asyncio.sleep(SLEEP_FADE_SECONDS / SLEEP_FADE_STEPS)
                level = round(original * (1 - step / SLEEP_FADE_STEPS) * 100)
                await player.async_set_volume(level)
        finally:
            self._fading = False
        await self._stop_for_parental(card)
        await player.async_set_volume(round(original * 100))
        self._restore_volume = None

    @callback
    def _handle_player(self, event: Event[EventStateChangedData]) -> None:
        """Maximallautstärke durchsetzen, solange eine Karte aufliegt."""
        settings = self.settings
        if not settings.parental or not settings.max_volume or self.hass.is_stopping:
            return
        if self._fading:
            return
        volume = _volume_level(event.data["new_state"])
        limit = settings.max_volume / 100
        if volume is None or volume <= limit + VOLUME_TOLERANCE:
            return
        if self._card_on_reader() is None:
            return
        _LOGGER.info(
            "%s: Lautstärke %d %% über Maximum, setze %d %%",
            self.name,
            round(volume * 100),
            settings.max_volume,
        )
        self.hass.async_create_background_task(
            self._limit_volume(settings.max_volume), f"nfc_musikbox {self.name} Lautstärke"
        )

    async def _limit_volume(self, percent: int) -> None:
        try:
            await self.player.async_set_volume(percent)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: Maximallautstärke nicht gesetzt: %s", self.name, err)

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

    def _button_blocked(self) -> bool:
        """Gesperrte Karte: Tasten dürfen nur noch pausieren."""
        card = self._card_on_reader()
        if card is None:
            return False
        return card.tag_id == self._blocked or self._quiet_blocks(card)

    async def _on_play_button(self) -> None:
        if self._button_blocked():
            state = self.player.state
            if state is not None and state.state == STATE_PLAYING:
                await self.player.async_pause()
            else:
                self._on_locked()
            return
        await self.player.async_play_pause()

    async def _on_back_short(self) -> None:
        if self._button_blocked():
            self._on_locked()
            return
        state = self.player.state
        if state is None or state.state not in (STATE_PLAYING, STATE_PAUSED):
            return
        target = max(0, round(current_position(state) - float(self.options[OPT_SKIP_BACK])))
        await self.player.async_seek(target)

    async def _on_back_long(self) -> None:
        if self._button_blocked():
            self._on_locked()
            return
        await self.player.async_restart()


def _volume_level(state: State | None) -> float | None:
    if state is None:
        return None
    value = state.attributes.get("volume_level")
    return float(value) if isinstance(value, int | float) else None

"""Zustandsmaschine pro Lesegerät: Karte auflegen, entfernen, wechseln."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import (
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
from .limits import LimitReason, LimitTracker
from .parents import ParentNotifier
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
from .store import TITLE_MAX_LEN, Card, CardStore, ReaderSettings

_LOGGER = logging.getLogger(__name__)

IGNORED_STATES = {STATE_UNAVAILABLE, STATE_UNKNOWN, ""}

# Schlaf-Timer: Ausblenden über diese Dauer in so vielen Schritten
SLEEP_FADE_SECONDS = 15.0
SLEEP_FADE_STEPS = 10
# Toleranz, bevor die Maximallautstärke eingreift (Rundung der Player)
VOLUME_TOLERANCE = 0.005

# Gründe für eine gesperrte Karte außer den Tageslimits
BLOCK_SLEEP = "sleep"


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
        *,
        on_locked: Callable[[], None] | None = None,
        limits: LimitTracker | None = None,
        notifier: ParentNotifier | None = None,
    ) -> None:
        self.hass = hass
        self.reader = reader
        self.store = store
        self.options = options
        self._on_locked = on_locked or (lambda: None)
        self.limits = limits
        self.notifier = notifier
        self._task: asyncio.Task[None] | None = None
        # Karte, deren Fortsetzen gerade läuft; ihre gemerkte Stelle ist dann noch gültig
        self._restoring: str | None = None
        self._unsub: list[Any] = []
        # Kindersicherung
        # Karte, die wegen Tageslimit/Schlaf-Timer gesperrt ist, und der Grund
        self._blocked: str | None = None
        self._block_reason: str | None = None
        self._sleep_unsub: CALLBACK_TYPE | None = None
        self._sleep_deadline: datetime | None = None
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
        if self.limits is not None:
            self._unsub.append(self.limits.async_add_listener(self._update_live))
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
        if self.limits is not None:
            self.limits.stop_session(self.reader.subentry_id)
        if self.notifier is not None:
            self.notifier.live_end(self.reader)
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
        self._set_blocked(None)
        interrupted_restore = self._restoring
        previous = self._task
        self._task = self.hass.async_create_background_task(
            self._run(removed, placed, previous, interrupted_restore, blocked_removed),
            f"nfc_musikbox {self.name}",
        )
        self._sync_session()

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
            reason = self._limit_reason(placed_card) if placed_card is not None else None
            if placed_card is not None and reason is not None:
                self._block_for_limit(placed_card, reason)
                if removed_card is not None:
                    # Kartenwechsel über dem Limit: die alte Karte nicht weiterlaufen lassen
                    await self._pause_if_playing()
                return
            if placed_card is not None:
                if removed is not None:
                    await asyncio.sleep(float(self.options[OPT_CARD_CHANGE_DELAY]))
                await self._start_card(placed_card)
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

    @property
    def _parental(self) -> bool:
        return self.settings.parental

    def _limit_reason(self, card: Card) -> LimitReason | None:
        if self.limits is None or not self._parental:
            return None
        return self.limits.check(card)

    @callback
    def _set_blocked(self, card: Card | None, reason: str | None = None) -> None:
        self._blocked = card.tag_id if card is not None else None
        self._block_reason = reason if card is not None else None

    @callback
    def _block_for_limit(self, card: Card, reason: LimitReason) -> None:
        _LOGGER.info("%s: %s gesperrt, Tageslimit (%s)", self.name, card.name, reason)
        self._set_blocked(card, reason)
        self._on_locked()
        self._sync_session()
        self._update_live()
        if self.notifier is not None:
            self.notifier.request(self.reader, card, reason)

    async def _start_card(self, card: Card) -> None:
        """Karte starten und bei den Tageslimits anrechnen."""
        if self.limits is not None and self._parental:
            self.limits.register_start(card)
        await self._on_placed(card)
        self._start_sleep_timer(card)

    def _card_on_reader(self) -> Card | None:
        state = self.hass.states.get(self.reader.card_sensor or "")
        return self._active_card(state.state if state else None)

    async def _pause_if_playing(self) -> None:
        state = self.player.state
        if state is not None and state.state == STATE_PLAYING:
            await self.player.async_pause()

    @callback
    def _sync_session(self) -> None:
        """Hörzeit zählt, solange eine Karte mit Limit aufliegt und der Player spielt."""
        if self.limits is None:
            return
        card = self._card_on_reader()
        state = self.player.state
        listening = (
            self._parental
            and card is not None
            and card.limited
            and card.tag_id != self._blocked
            and state is not None
            and state.state == STATE_PLAYING
        )
        if listening:
            self.limits.start_session(self.reader.subentry_id, self._time_up)
        else:
            self.limits.stop_session(self.reader.subentry_id)

    @callback
    def _time_up(self) -> None:
        card = self._card_on_reader()
        if card is None or not card.limited or card.tag_id == self._blocked:
            self._sync_session()
            return
        _LOGGER.info("%s: Hörzeit aufgebraucht, blende %s aus", self.name, card.name)
        self._set_blocked(card, LimitReason.TIME)
        self._run_exclusive(self._fade_and_stop(card, LimitReason.TIME), "Hörzeit")
        if self.notifier is not None:
            self.notifier.request(self.reader, card, LimitReason.TIME, stopped=True)

    @callback
    def grant(self, minutes: int) -> None:
        """Freigabe der Eltern (Mitteilung): verlängern und eine gesperrte Karte starten."""
        if self.limits is None:
            return
        card = self._card_on_reader()
        waiting = (
            card
            if card is not None
            and card.tag_id == self._blocked
            and self._block_reason in tuple(LimitReason)
            else None
        )
        self.limits.grant(minutes, waiting)
        if waiting is not None:
            self._resume_if_allowed(waiting)

    @callback
    def _resume_if_allowed(self, card: Card) -> bool:
        if self._limit_reason(card) is not None:
            return False
        _LOGGER.info("%s: %s freigegeben, starte", self.name, card.name)
        self._set_blocked(None)
        self._run_exclusive(self._start_card(card), "Freigabe")
        return True

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

    async def _stop_for_parental(self, card: Card, reason: str) -> None:
        """Stelle merken (Hörspiel) und pausieren, wie beim Abziehen; Karte sperren."""
        self._cancel_sleep_timer()
        self._set_blocked(card, reason)
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
            self._sleep_deadline = None
            if self._card_on_reader() is None:
                return
            _LOGGER.info("%s: Schlaf-Timer abgelaufen, blende %s aus", self.name, card.name)
            self._run_exclusive(self._fade_and_stop(card, BLOCK_SLEEP), "Schlaf-Timer")

        self._sleep_unsub = async_call_later(self.hass, minutes * 60, _expired)
        self._sleep_deadline = dt_util.utcnow() + timedelta(minutes=minutes)
        self._update_live()

    @callback
    def _cancel_sleep_timer(self) -> None:
        self._sleep_deadline = None
        if self._sleep_unsub is not None:
            self._sleep_unsub()
            self._sleep_unsub = None

    async def _fade_and_stop(self, card: Card, reason: str) -> None:
        """Ausblenden, dann wie Abziehen stoppen und die Karte sperren."""
        player = self.player
        state = player.state
        if state is None or state.state != STATE_PLAYING:
            self._set_blocked(card, reason)
            self._sync_session()
            return
        original = _volume_level(state)
        if original is None or not player.supports(MediaPlayerEntityFeature.VOLUME_SET):
            await self._stop_for_parental(card, reason)
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
        await self._stop_for_parental(card, reason)
        await player.async_set_volume(round(original * 100))
        self._restore_volume = None

    @callback
    def _handle_player(self, event: Event[EventStateChangedData]) -> None:
        self._sync_session()
        old, new = event.data["old_state"], event.data["new_state"]
        if _live_relevant(old) != _live_relevant(new):
            self._update_live()
        self._enforce_max_volume(event)

    @callback
    def _enforce_max_volume(self, event: Event[EventStateChangedData]) -> None:
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

    # ---------- Live-Aktivität ----------

    @callback
    def _update_live(self) -> None:
        """Live-Aktivität für die Eltern: läuft, solange eine Karte mit Limit spielt."""
        notifier = self.notifier
        if notifier is None:
            return
        card = self._card_on_reader()
        state = self.player.state
        if (
            not self.store.parental.live_activity
            or not self._parental
            or card is None
            or not card.limited
            or card.tag_id == self._blocked
            or state is None
            or state.state not in (STATE_PLAYING, STATE_PAUSED)
        ):
            notifier.live_end(self.reader)
            return
        message, data = self._live_content(state)
        notifier.live_update(self.reader, card.name, message, data)

    def _stop_window(self) -> tuple[datetime, float] | None:
        """Zeitpunkt, an dem die Wiedergabe endet (Hörzeit/Schlaf-Timer), und Gesamtdauer."""
        windows: list[tuple[datetime, float]] = []
        now = dt_util.utcnow()
        if self.limits is not None:
            remaining = self.limits.remaining_seconds()
            allowed = self.limits.allowed_seconds
            if remaining is not None and allowed:
                windows.append((now + timedelta(seconds=remaining), allowed))
        if self._sleep_deadline is not None:
            windows.append((self._sleep_deadline, self.settings.sleep_timer * 60.0))
        return min(windows, key=lambda w: w[0]) if windows else None

    def _live_content(self, state: State) -> tuple[str, dict[str, Any]]:
        playing = state.state == STATE_PLAYING
        q = queue_position(state)
        size = state.attributes.get("queue_size")
        if q and isinstance(size, int) and size > 1:
            chapter = f"Kapitel {q} von {size}"
        else:
            chapter = f"Kapitel {q}" if q > 1 else ""
        parts = [None if playing else "Pausiert", chapter, media_title(state)]
        message = " · ".join(p for p in parts if p) or "Läuft"
        data: dict[str, Any] = {}
        window = self._stop_window()
        if window is not None:
            end, total = window
            remaining = max(0.0, (end - dt_util.utcnow()).total_seconds())
            data = {
                "progress": round(remaining),
                "progress_max": max(1, round(total)),
                "progress_bar_direction": "decreasing",
                "critical_text": f"noch {math.ceil(remaining / 60)} min",
            }
            if playing:
                data |= {"chronometer": True, "when": round(end.timestamp())}
        elif q and isinstance(size, int) and size > 1:
            data = {"progress": q, "progress_max": size}
        return message, data

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
        return card is not None and card.tag_id == self._blocked

    async def _on_play_button(self) -> None:
        if self._button_blocked():
            state = self.player.state
            if state is not None and state.state == STATE_PLAYING:
                await self.player.async_pause()
                return
            card = self._card_on_reader()
            reason = self._block_reason
            if card is not None and reason in tuple(LimitReason):
                # Limits inzwischen zurückgesetzt/verlängert: Play startet die Karte
                if self._resume_if_allowed(card):
                    return
                self._on_locked()
                if self.notifier is not None:
                    self.notifier.request(self.reader, card, LimitReason(reason))
                return
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


def _live_relevant(state: State | None) -> tuple[Any, ...]:
    """Was die Live-Aktivität zeigt; Positions-Updates allein lösen kein Update aus."""
    if state is None:
        return ()
    return (
        state.state,
        state.attributes.get("queue_position"),
        state.attributes.get("queue_size"),
        state.attributes.get("media_title"),
    )


def _volume_level(state: State | None) -> float | None:
    if state is None:
        return None
    value = state.attributes.get("volume_level")
    return float(value) if isinstance(value, int | float) else None

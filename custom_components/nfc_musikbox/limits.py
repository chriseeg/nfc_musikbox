"""Tageslimits der Kindersicherung: Anzahl Hörspiele und Hörzeit, über alle Lesegeräte."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.util import dt as dt_util

from .store import Card, CardStore, DailyUsage, StoreEvent

_LOGGER = logging.getLogger(__name__)


class LimitReason(StrEnum):
    """Warum eine Karte gesperrt ist."""

    COUNT = "count"  # Anzahl Hörspiele erreicht
    TIME = "time"  # Hörzeit aufgebraucht


class LimitTracker:
    """Zählt gestartete Hörspiele und Hörzeit pro Tag.

    Als Hörzeit zählt, solange an einem Lesegerät eine Karte mit Limit aufliegt
    und der Player spielt ("Sitzung"). Laufen Sitzungen, meldet ein Timer das
    Ende der Hörzeit an die betroffenen Lesegeräte.
    """

    def __init__(self, hass: HomeAssistant, store: CardStore) -> None:
        self.hass = hass
        self.store = store
        # Lesegerät -> (Beginn der Sitzung, Rückruf bei aufgebrauchter Hörzeit)
        self._sessions: dict[str, tuple[datetime, Callable[[], None]]] = {}
        self._timer: CALLBACK_TYPE | None = None
        self._unsub: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []

    @callback
    def async_start(self) -> None:
        self._unsub.append(
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=0)
        )
        self._unsub.append(self.store.async_add_listener(self._handle_store))
        self.usage  # noqa: B018 - Tageswechsel seit dem letzten Lauf übernehmen

    @callback
    def async_stop(self) -> None:
        """Laufende Sitzungen verbuchen, damit ein Reload keine Hörzeit verliert."""
        for reader_id in list(self._sessions):
            self.stop_session(reader_id)
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        self._cancel_timer()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Rückruf, wenn sich die verbleibende Hörzeit verschiebt (Sitzungen, Freigaben)."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _changed(self) -> None:
        self._reschedule()
        for listener in list(self._listeners):
            listener()

    @callback
    def _handle_store(self, event: StoreEvent, _key: str) -> None:
        if event in (StoreEvent.PARENTAL, StoreEvent.USAGE):
            self._changed()

    # ---------- Stand ----------

    @staticmethod
    def _today() -> str:
        return dt_util.now().date().isoformat()

    @property
    def usage(self) -> DailyUsage:
        """Verbrauch von heute; nach Mitternacht beginnt ein neuer Tag."""
        usage = self.store.usage
        today = self._today()
        if usage.day != today:
            if usage.day:
                _LOGGER.debug("Neuer Tag, Tageslimits zurückgesetzt")
            self._restart_sessions()
            usage = DailyUsage(day=today)
            self.store.set_usage(usage)
        return usage

    @property
    def allowed_count(self) -> int | None:
        limit = self.store.parental.daily_count
        return limit + self.usage.extra_count if limit else None

    @property
    def allowed_seconds(self) -> float | None:
        limit = self.store.parental.daily_minutes
        return (limit + self.usage.extra_minutes) * 60.0 if limit else None

    @property
    def count(self) -> int:
        return len(self.usage.cards)

    def used_seconds(self) -> float:
        now = dt_util.utcnow()
        running = sum((now - since).total_seconds() for since, _ in self._sessions.values())
        return self.usage.seconds + running

    def remaining_seconds(self) -> float | None:
        allowed = self.allowed_seconds
        return None if allowed is None else max(0.0, allowed - self.used_seconds())

    def check(self, card: Card) -> LimitReason | None:
        """Grund, warum die Karte jetzt nicht starten darf, sonst None."""
        if not card.limited:
            return None
        remaining = self.remaining_seconds()
        if remaining is not None and remaining <= 0:
            return LimitReason.TIME
        allowed = self.allowed_count
        if allowed is not None and card.tag_id not in self.usage.cards and self.count >= allowed:
            return LimitReason.COUNT
        return None

    def as_dict(self) -> dict[str, Any]:
        """Für Panel, Sensoren und Diagnose."""
        return {
            "count": self.count,
            "allowed_count": self.allowed_count,
            "seconds": round(self.used_seconds()),
            "allowed_seconds": self.allowed_seconds,
            "extra_count": self.usage.extra_count,
            "extra_minutes": self.usage.extra_minutes,
            "cards": list(self.usage.cards),
        }

    # ---------- Änderungen ----------

    @callback
    def register_start(self, card: Card) -> None:
        """Karte mit Limit gestartet: zählt einmal pro Tag."""
        usage = self.usage
        if not card.limited or card.tag_id in usage.cards:
            return
        self.store.set_usage(replace(usage, cards=[*usage.cards, card.tag_id]))
        _LOGGER.info("Tageslimit: %s ist Hörspiel Nr. %d", card.name, self.count)

    @callback
    def start_session(self, reader_id: str, on_time_up: Callable[[], None]) -> None:
        if reader_id in self._sessions:
            return
        self.usage  # noqa: B018
        self._sessions[reader_id] = (dt_util.utcnow(), on_time_up)
        self._changed()

    @callback
    def stop_session(self, reader_id: str) -> None:
        session = self._sessions.pop(reader_id, None)
        if session is None:
            return
        elapsed = (dt_util.utcnow() - session[0]).total_seconds()
        usage = self.store.usage
        self.store.set_usage(replace(usage, seconds=usage.seconds + max(0.0, elapsed)))

    def is_listening(self, reader_id: str) -> bool:
        return reader_id in self._sessions

    @callback
    def grant(self, minutes: int, card: Card | None = None) -> None:
        """Freigabe der Eltern: Hörzeit verlängern und/oder diese Karte zusätzlich erlauben."""
        usage = self.usage
        extra_count, cards = usage.extra_count, usage.cards
        if card is not None and card.limited and card.tag_id not in cards:
            allowed = self.allowed_count
            if allowed is not None and self.count >= allowed:
                extra_count += 1
            cards = [*cards, card.tag_id]
        self.store.set_usage(
            replace(
                usage,
                cards=cards,
                extra_count=extra_count,
                extra_minutes=usage.extra_minutes + max(0, minutes),
            )
        )
        _LOGGER.info("Tageslimit freigegeben: +%d min%s", minutes, f", {card.name}" if card else "")

    @callback
    def extend(self, count: int = 0, minutes: int = 0) -> None:
        usage = self.usage
        self.store.set_usage(
            replace(
                usage,
                extra_count=usage.extra_count + max(0, count),
                extra_minutes=usage.extra_minutes + max(0, minutes),
            )
        )

    @callback
    def reset(self) -> None:
        """Zähler von heute auf null (Freigaben verfallen ebenfalls)."""
        self._restart_sessions()
        self.store.set_usage(DailyUsage(day=self._today()))
        _LOGGER.info("Tageslimits zurückgesetzt")

    @callback
    def _restart_sessions(self) -> None:
        now = dt_util.utcnow()
        for reader_id, (_, on_time_up) in list(self._sessions.items()):
            self._sessions[reader_id] = (now, on_time_up)

    @callback
    def _midnight(self, _now: datetime) -> None:
        self.usage  # noqa: B018

    # ---------- Timer ----------

    @callback
    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _reschedule(self) -> None:
        self._cancel_timer()
        remaining = self.remaining_seconds()
        if not self._sessions or remaining is None:
            return
        self._timer = async_call_later(self.hass, max(remaining, 0.0), self._time_up)

    @callback
    def _time_up(self, _now: datetime) -> None:
        self._timer = None
        remaining = self.remaining_seconds()
        if remaining is not None and remaining > 0:
            # Inzwischen verlängert, aber der Store hat (noch) nichts gemeldet
            self._reschedule()
            return
        _LOGGER.info("Hörzeit für heute aufgebraucht")
        for _, on_time_up in list(self._sessions.values()):
            on_time_up()

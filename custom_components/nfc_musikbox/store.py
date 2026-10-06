"""Persistenz der Karten und gemerkten Positionen."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
import logging
from typing import Any, Literal

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

# Intern "tonie"/"simple", angezeigt als Hörspiel-Modus/Musik-Modus
type CardMode = Literal["tonie", "simple"]
CARD_MODES: tuple[CardMode, ...] = ("tonie", "simple")
type RepeatMode = Literal["off", "all", "one"]
REPEAT_MODES: tuple[RepeatMode, ...] = ("off", "all", "one")
TITLE_MAX_LEN = 60

# Positionen ändern sich bei jedem Herausziehen; gebündelt speichern schont den eMMC.
POSITION_SAVE_DELAY = 5


class StoreEvent(StrEnum):
    """Art der Änderung, die an Listener gemeldet wird."""

    CARD_ADDED = "card_added"
    CARD_UPDATED = "card_updated"
    CARD_REMOVED = "card_removed"
    READER = "reader"
    PARENTAL = "parental"  # Einstellungen der Kindersicherung
    USAGE = "usage"  # Tagesverbrauch
    POSITION = "position"
    SEEN = "seen"


type StoreListener = Callable[[StoreEvent, str], None]


@dataclass(slots=True)
class SeenTag:
    """Zuletzt gescannte Karte (auch ohne Zuordnung) für "Noch ohne Musik"."""

    last: str
    reader: str | None = None


@dataclass(slots=True)
class ReaderSettings:
    """Einstellungen pro Lesegerät, die ohne Reload änderbar sind."""

    # Lautstärke in Prozent beim Auflegen einer Karte; 0 = nicht ändern
    start_volume: int = 0
    # Kindersicherung: Hauptschalter und Bausteine (0/None = Baustein aus)
    parental: bool = True
    max_volume: int = 0
    sleep_timer: int = 0  # Minuten

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ReaderSettings:
        def percent(key: str) -> int:
            value = raw.get(key, 0)
            return min(100, max(0, int(value))) if isinstance(value, int | float) else 0

        sleep = raw.get("sleep_timer", 0)
        return cls(
            start_volume=percent("start_volume"),
            parental=bool(raw.get("parental", True)),
            max_volume=percent("max_volume"),
            sleep_timer=min(240, max(0, int(sleep))) if isinstance(sleep, int | float) else 0,
        )


def _clamped_int(value: Any, maximum: int) -> int:
    return min(maximum, max(0, int(value))) if isinstance(value, int | float) else 0


@dataclass(slots=True)
class ParentalSettings:
    """Globale Kindersicherung: Tageslimits und Mitteilungen an die Eltern."""

    # 0 = kein Limit
    daily_count: int = 0  # verschiedene Hörspiele pro Tag
    daily_minutes: int = 0  # Hörzeit pro Tag
    # Notify-Dienste ohne Domain, z. B. "mobile_app_iphone_von_christian"
    notify: list[str] = field(default_factory=list)
    live_activity: bool = False

    @property
    def has_limits(self) -> bool:
        return bool(self.daily_count or self.daily_minutes)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ParentalSettings:
        notify = raw.get("notify")
        return cls(
            daily_count=_clamped_int(raw.get("daily_count"), 50),
            daily_minutes=_clamped_int(raw.get("daily_minutes"), 1440),
            notify=[n for n in notify if isinstance(n, str) and n]
            if isinstance(notify, list)
            else [],
            live_activity=bool(raw.get("live_activity", False)),
        )


@dataclass(slots=True)
class DailyUsage:
    """Verbrauch des Tages (lokales Datum), über alle Lesegeräte gezählt."""

    day: str = ""
    cards: list[str] = field(default_factory=list)  # heute gestartete Karten
    seconds: float = 0.0  # gehörte Zeit (Player spielt, Karte mit Limit liegt auf)
    # Von den Eltern freigegeben, zusätzlich zu den Limits
    extra_count: int = 0
    extra_minutes: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DailyUsage:
        cards = raw.get("cards")
        seconds = raw.get("seconds")
        return cls(
            day=str(raw.get("day", "")),
            cards=[c for c in cards if isinstance(c, str)] if isinstance(cards, list) else [],
            seconds=float(seconds) if isinstance(seconds, int | float) else 0.0,
            extra_count=_clamped_int(raw.get("extra_count"), 1000),
            extra_minutes=_clamped_int(raw.get("extra_minutes"), 100000),
        )


@dataclass(slots=True)
class Position:
    """Gemerkte Stelle einer Karte: Queue-Position (1-basiert), Sekunde, Titel."""

    q: int
    p: int
    t: str

    def __post_init__(self) -> None:
        self.t = self.t[:TITLE_MAX_LEN]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Position:
        return cls(q=int(data.get("q", 0)), p=int(data.get("p", 0)), t=str(data.get("t", "")))


@dataclass(slots=True)
class Card:
    """Eine NFC-Karte mit ihrem Medium."""

    tag_id: str
    name: str
    media: dict[str, Any] | None = None
    mode: CardMode = "tonie"
    enabled: bool = True
    # Subentry-IDs der Lesegeräte; leer = an allen Lesegeräten
    readers: list[str] = field(default_factory=list)
    # Nur im Musik-Modus; None = Einstellung des Players nicht ändern
    shuffle: bool | None = None
    repeat: RepeatMode | None = None
    # Kindersicherung: Karte zählt zum Tageslimit (Anzahl und Hörzeit)
    limited: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Card:
        mode = data.get("mode", "tonie")
        shuffle = data.get("shuffle")
        repeat = data.get("repeat")
        return cls(
            tag_id=data["tag_id"],
            name=data.get("name") or data["tag_id"],
            media=data.get("media"),
            mode=mode if mode in CARD_MODES else "tonie",
            enabled=bool(data.get("enabled", True)),
            readers=list(data.get("readers", [])),
            shuffle=shuffle if isinstance(shuffle, bool) else None,
            repeat=repeat if repeat in REPEAT_MODES else None,
            limited=bool(data.get("limited", False)),
        )

    def works_on(self, reader_id: str) -> bool:
        return not self.readers or reader_id in self.readers


class CardStore:
    """Karten und Positionen, gespeichert unter .storage/nfc_musikbox."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.cards: dict[str, Card] = {}
        self.positions: dict[str, Position] = {}
        self.seen: dict[str, SeenTag] = {}
        self.reader_settings: dict[str, ReaderSettings] = {}
        self.parental = ParentalSettings()
        self.usage = DailyUsage()
        self._dirty = False
        self._listeners: list[StoreListener] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.cards = {}
        for raw in data.get("cards", []):
            try:
                card = Card.from_dict(raw)
            except KeyError, TypeError, ValueError:
                _LOGGER.warning("Ungültiger Karteneintrag ignoriert: %s", raw)
                continue
            self.cards[card.tag_id] = card
        self.positions = {}
        for tag_id, raw in data.get("positions", {}).items():
            try:
                self.positions[tag_id] = Position.from_dict(raw)
            except TypeError, ValueError:
                _LOGGER.warning("Ungültige Position für %s ignoriert: %s", tag_id, raw)
        self.seen = {}
        for tag_id, raw in data.get("seen", {}).items():
            if isinstance(raw, dict) and isinstance(raw.get("last"), str):
                self.seen[tag_id] = SeenTag(last=raw["last"], reader=raw.get("reader"))
        self.reader_settings = {}
        for reader_id, raw in data.get("reader_settings", {}).items():
            if isinstance(raw, dict):
                self.reader_settings[reader_id] = ReaderSettings.from_dict(raw)
        raw_parental = data.get("parental")
        self.parental = ParentalSettings.from_dict(
            raw_parental if isinstance(raw_parental, dict) else {}
        )
        raw_usage = data.get("usage")
        self.usage = DailyUsage.from_dict(raw_usage if isinstance(raw_usage, dict) else {})
        _LOGGER.debug(
            "Store geladen: %d Karten, %d Positionen", len(self.cards), len(self.positions)
        )

    def _data(self) -> dict[str, Any]:
        return {
            "cards": [asdict(card) for card in self.cards.values()],
            "positions": {tag: asdict(pos) for tag, pos in self.positions.items()},
            "seen": {tag: asdict(seen) for tag, seen in self.seen.items()},
            "reader_settings": {r: asdict(rs) for r, rs in self.reader_settings.items()},
            "parental": asdict(self.parental),
            "usage": asdict(self.usage),
        }

    @callback
    def async_add_listener(self, listener: StoreListener) -> CALLBACK_TYPE:
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def _notify(self, event: StoreEvent, tag_id: str) -> None:
        for listener in list(self._listeners):
            listener(event, tag_id)

    async def async_save(self) -> None:
        self._dirty = False
        await self._store.async_save(self._data())

    async def async_flush(self) -> None:
        """Verzögert gespeicherte Positionen sofort schreiben (z. B. vor einem Reload)."""
        if self._dirty:
            await self.async_save()

    def _delayed_data(self) -> dict[str, Any]:
        self._dirty = False
        return self._data()

    def _schedule_save(self) -> None:
        self._dirty = True
        self._store.async_delay_save(self._delayed_data, POSITION_SAVE_DELAY)

    async def async_set_card(self, card: Card) -> None:
        added = card.tag_id not in self.cards
        self.cards[card.tag_id] = card
        await self.async_save()
        self._notify(StoreEvent.CARD_ADDED if added else StoreEvent.CARD_UPDATED, card.tag_id)

    async def async_update_card(self, tag_id: str, **changes: Any) -> Card:
        card = self.cards[tag_id]
        for key, value in changes.items():
            setattr(card, key, value)
        await self.async_save()
        self._notify(StoreEvent.CARD_UPDATED, tag_id)
        return card

    async def async_remove_card(self, tag_id: str) -> None:
        if self.cards.pop(tag_id, None) is None:
            return
        self.positions.pop(tag_id, None)
        await self.async_save()
        self._notify(StoreEvent.CARD_REMOVED, tag_id)

    def set_position(self, tag_id: str, position: Position | None) -> None:
        if position is None:
            if self.positions.pop(tag_id, None) is None:
                return
        else:
            self.positions[tag_id] = position
        self._schedule_save()
        self._notify(StoreEvent.POSITION, tag_id)

    def get_reader_settings(self, reader_id: str) -> ReaderSettings:
        return self.reader_settings.get(reader_id) or ReaderSettings()

    async def async_set_reader_settings(self, reader_id: str, **changes: Any) -> None:
        current = asdict(self.get_reader_settings(reader_id))
        self.reader_settings[reader_id] = ReaderSettings.from_dict({**current, **changes})
        await self.async_save()
        self._notify(StoreEvent.READER, reader_id)

    async def async_set_parental(self, **changes: Any) -> None:
        self.parental = ParentalSettings.from_dict({**asdict(self.parental), **changes})
        await self.async_save()
        self._notify(StoreEvent.PARENTAL, "")

    def set_usage(self, usage: DailyUsage) -> None:
        """Verbrauch ersetzen; gespeichert wird gebündelt (ändert sich oft)."""
        self.usage = usage
        self._schedule_save()
        self._notify(StoreEvent.USAGE, "")

    def mark_seen(self, tag_id: str, reader: str | None) -> None:
        self.seen[tag_id] = SeenTag(last=dt_util.utcnow().isoformat(), reader=reader)
        self._schedule_save()
        self._notify(StoreEvent.SEEN, tag_id)

    def forget_seen(self, tag_id: str) -> None:
        if self.seen.pop(tag_id, None) is not None:
            self._schedule_save()
            self._notify(StoreEvent.SEEN, tag_id)

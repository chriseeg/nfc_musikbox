"""Mitteilungen an die Eltern: Freigabe-Anfragen und Live-Aktivität.

Gesendet wird an die Notify-Dienste der Companion-App (`notify.mobile_app_*`),
die in der Kindersicherung ausgewählt sind.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .limits import LimitReason, LimitTracker
from .reader import ReaderConfig
from .store import Card, CardStore

_LOGGER = logging.getLogger(__name__)

NOTIFY_DOMAIN = "notify"
EVENT_NOTIFICATION_ACTION = "mobile_app_notification_action"
ACTION_PREFIX = "NFC_MUSIKBOX_"
GRANT_MINUTES = (15, 30)
# Dieselbe Anfrage (Lesegerät + Karte) höchstens so oft senden
REQUEST_THROTTLE_SECONDS = 60.0
# Updates der Live-Aktivität bündeln; iOS drosselt zu häufige Pushes
LIVE_DEBOUNCE_SECONDS = 3.0

type GrantHandler = Callable[[str, int], None]


def _tag(prefix: str, reader_id: str) -> str:
    # Tag: nur Buchstaben, Ziffern, "-" und "_", höchstens 64 Zeichen
    return f"nfc_musikbox_{prefix}_{reader_id}".lower()[:64]


def notify_services(hass: HomeAssistant) -> list[str]:
    """Notify-Dienste der Companion-App, die Aktionen und Live-Aktivitäten können."""
    return sorted(
        name
        for name in hass.services.async_services_for_domain(NOTIFY_DOMAIN)
        if name.startswith("mobile_app_")
    )


class ParentNotifier:
    """Sendet Anfragen/Live-Aktivitäten und wertet die Antworten aus."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: CardStore,
        limits: LimitTracker,
        readers: dict[str, ReaderConfig],
    ) -> None:
        self.hass = hass
        self.store = store
        self.limits = limits
        self.readers = readers
        self._on_grant: GrantHandler | None = None
        self._unsub: CALLBACK_TYPE | None = None
        self._last_request: dict[str, tuple[str, float]] = {}
        # Live-Aktivität: aktive Lesegeräte, ausstehende Updates
        self._live_active: set[str] = set()
        self._live_last: dict[str, dict[str, Any]] = {}
        self._live_pending: dict[str, tuple[dict[str, Any], CALLBACK_TYPE]] = {}

    @callback
    def async_start(self, on_grant: GrantHandler) -> None:
        self._on_grant = on_grant
        self._unsub = self.hass.bus.async_listen(EVENT_NOTIFICATION_ACTION, self._handle_action)

    @callback
    def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        for _, cancel in self._live_pending.values():
            cancel()
        self._live_pending.clear()

    @property
    def targets(self) -> list[str]:
        available = self.hass.services.async_services_for_domain(NOTIFY_DOMAIN)
        return [t for t in self.store.parental.notify if t in available]

    @callback
    def _send(self, payload: dict[str, Any], label: str) -> None:
        for target in self.targets:
            self.hass.async_create_background_task(
                self._call(target, payload, label), f"nfc_musikbox {label}"
            )

    async def _call(self, target: str, payload: dict[str, Any], label: str) -> None:
        try:
            await self.hass.services.async_call(NOTIFY_DOMAIN, target, payload, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("%s an %s nicht gesendet: %s", label, target, err)

    @callback
    def _clear(self, tag: str) -> None:
        self._send({"message": "clear_notification", "data": {"tag": tag}}, "Mitteilung löschen")

    # ---------- Anfrage ----------

    @callback
    def request(
        self, reader: ReaderConfig, card: Card, reason: LimitReason, *, stopped: bool = False
    ) -> None:
        """Eltern fragen, ob die Karte trotz Limit laufen darf."""
        if not self.targets:
            return
        now = dt_util.utcnow().timestamp()
        last = self._last_request.get(reader.subentry_id)
        if (
            not stopped
            and last
            and last[0] == card.tag_id
            and now - last[1] < REQUEST_THROTTLE_SECONDS
        ):
            return
        self._last_request[reader.subentry_id] = (card.tag_id, now)
        limits = self.limits
        if reason is LimitReason.TIME:
            used = round(limits.used_seconds() / 60)
            text = (
                f"Hörzeit für heute aufgebraucht ({used} min). „{card.name}“ wurde angehalten."
                if stopped
                else f"„{card.name}“ möchte gehört werden, aber die Hörzeit für heute "
                f"ist aufgebraucht ({used} min)."
            )
        else:
            text = (
                f"„{card.name}“ möchte gehört werden. Heute liefen schon "
                f"{limits.count} von {limits.allowed_count} Hörspielen."
            )
        actions: list[dict[str, Any]] = []
        if reason is LimitReason.COUNT:
            actions.append({"action": self._action_id("GRANT", 0, reader), "title": "Erlauben"})
        if self.store.parental.daily_minutes:
            actions += [
                {"action": self._action_id("GRANT", m, reader), "title": f"+{m} min"}
                for m in GRANT_MINUTES
            ]
        actions.append(
            {"action": self._action_id("DENY", 0, reader), "title": "Ablehnen", "destructive": True}
        )
        _LOGGER.info("%s: frage Eltern wegen %s (%s)", reader.title, card.name, reason)
        self._send(
            {
                "title": f"Musikbox {reader.title}",
                "message": text,
                "data": {
                    "tag": _tag("request", reader.subentry_id),
                    "group": "nfc_musikbox",
                    "actions": actions,
                    "push": {"interruption-level": "time-sensitive"},
                },
            },
            "Anfrage",
        )

    @staticmethod
    def _action_id(kind: str, minutes: int, reader: ReaderConfig) -> str:
        return f"{ACTION_PREFIX}{kind}_{minutes}_{reader.subentry_id}"

    @callback
    def _handle_action(self, event: Event[dict[str, Any]]) -> None:
        action = event.data.get("action")
        if not isinstance(action, str) or not action.startswith(ACTION_PREFIX):
            return
        parts = action.removeprefix(ACTION_PREFIX).split("_", 2)
        if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in self.readers:
            _LOGGER.debug("Unbekannte Aktion %s", action)
            return
        kind, minutes, reader_id = parts[0], int(parts[1]), parts[2]
        self._last_request.pop(reader_id, None)
        # Auf den anderen Geräten ist die Frage damit beantwortet
        self._clear(_tag("request", reader_id))
        if kind == "GRANT" and self._on_grant is not None:
            self._on_grant(reader_id, minutes)
        elif kind == "DENY":
            _LOGGER.info("%s: Eltern haben abgelehnt", self.readers[reader_id].title)

    # ---------- Live-Aktivität ----------

    @callback
    def live_update(
        self, reader: ReaderConfig, title: str, message: str, data: dict[str, Any]
    ) -> None:
        """Live-Aktivität starten oder aktualisieren (gebündelt)."""
        reader_id = reader.subentry_id
        payload: dict[str, Any] = {
            "title": title,
            "message": message,
            "data": {
                "tag": _tag("live", reader_id),
                "live_update": True,
                "notification_icon": "mdi:book-music",
                **data,
            },
        }
        pending = self._live_pending.get(reader_id)
        if pending is not None:
            if pending[0] == payload:
                return
            pending[1]()
        elif reader_id in self._live_active and self._live_last.get(reader_id) == payload:
            return

        @callback
        def _flush(_now: Any) -> None:
            self._live_pending.pop(reader_id, None)
            self._live_last[reader_id] = payload
            if reader_id in self._live_active:
                # Nur der Start soll einen Ton machen
                sent = {**payload, "data": {**payload["data"], "silent": True}}
            else:
                sent = payload
            self._live_active.add(reader_id)
            self._send(sent, "Live-Aktivität")

        self._live_pending[reader_id] = (
            payload,
            async_call_later(self.hass, LIVE_DEBOUNCE_SECONDS, _flush),
        )

    @callback
    def live_end(self, reader: ReaderConfig) -> None:
        reader_id = reader.subentry_id
        pending = self._live_pending.pop(reader_id, None)
        if pending is not None:
            pending[1]()
        if reader_id in self._live_active:
            self._live_active.discard(reader_id)
            self._live_last.pop(reader_id, None)
            self._clear(_tag("live", reader_id))

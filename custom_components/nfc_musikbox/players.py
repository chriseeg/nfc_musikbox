"""Player-Backends: Abspielen, Position lesen und an der gemerkten Stelle fortsetzen."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_POSITION,
    ATTR_MEDIA_POSITION_UPDATED_AT,
    ATTR_MEDIA_REPEAT,
    ATTR_MEDIA_SEEK_POSITION,
    ATTR_MEDIA_SHUFFLE,
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_PLAY_PAUSE,
    SERVICE_MEDIA_SEEK,
    SERVICE_REPEAT_SET,
    SERVICE_SHUFFLE_SET,
    SERVICE_VOLUME_SET,
    STATE_PAUSED,
    STATE_PLAYING,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    OPT_QUEUE_JUMP_TIMEOUT,
    OPT_RESTORE_SETTLE,
    OPT_RESTORE_START_TIMEOUT,
    OPT_SEEK_ATTEMPTS,
    OPT_SEEK_RETRY_DELAY,
    OPT_SEEK_TOLERANCE,
)
from .store import Position

_LOGGER = logging.getLogger(__name__)

SONOS_DOMAIN = "sonos"
SONOS_SERVICE_PLAY_QUEUE = "play_queue"
ATTR_QUEUE_POSITION = "queue_position"

# Unterhalb dieser Stelle lohnt kein Spulen, das Medium startet ohnehin dort
MIN_RESTORE_SECONDS = 5


def queue_position(state: State | None) -> int:
    """Queue-Position (Sonos, 1-basiert); 0 wenn unbekannt."""
    if state is None:
        return 0
    try:
        return int(state.attributes.get(ATTR_QUEUE_POSITION) or 0)
    except TypeError, ValueError:
        return 0


def media_title(state: State | None) -> str:
    if state is None:
        return ""
    return str(state.attributes.get(ATTR_MEDIA_TITLE) or "")


def current_position(state: State | None, now: datetime | None = None) -> float:
    """Aktuelle Wiedergabeposition in Sekunden, beim Abspielen hochgerechnet."""
    if state is None:
        return 0.0
    try:
        pos = float(state.attributes.get(ATTR_MEDIA_POSITION) or 0)
    except TypeError, ValueError:
        pos = 0.0
    updated = state.attributes.get(ATTR_MEDIA_POSITION_UPDATED_AT)
    if state.state == STATE_PLAYING and updated is not None:
        if isinstance(updated, str):
            updated = dt_util.parse_datetime(updated)
        if isinstance(updated, datetime):
            pos += max(0.0, ((now or dt_util.utcnow()) - updated).total_seconds())
    return pos


def snapshot_position(state: State | None, rewind: float = 0.0) -> Position | None:
    """Merkbare Stelle des Players; None, wenn er weder spielt noch pausiert."""
    if state is None or state.state not in (STATE_PLAYING, STATE_PAUSED):
        return None
    pos = current_position(state)
    if state.state == STATE_PLAYING:
        pos -= rewind
    return Position(q=queue_position(state), p=max(0, round(pos)), t=media_title(state))


def fmt_position(pos: Position) -> str:
    """Lesbar, z. B. "Titel 2 · 1:10:10 · Kapitel 2"."""
    h, rest = divmod(pos.p, 3600)
    m, s = divmod(rest, 60)
    time = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    parts = [f"Titel {pos.q}"] if pos.q else []
    parts.append(time)
    if pos.t:
        parts.append(pos.t)
    return " · ".join(parts)


async def async_wait_for_state(
    hass: HomeAssistant,
    entity_id: str,
    predicate: Callable[[State | None], bool],
    wait_seconds: float,
) -> bool:
    """Warten, bis predicate(State) wahr ist. False bei Timeout."""
    if predicate(hass.states.get(entity_id)):
        return True
    done: asyncio.Future[None] = hass.loop.create_future()

    @callback
    def _changed(event: Event[EventStateChangedData]) -> None:
        if not done.done() and predicate(event.data["new_state"]):
            done.set_result(None)

    unsub = async_track_state_change_event(hass, entity_id, _changed)
    try:
        async with asyncio.timeout(wait_seconds):
            await done
    except TimeoutError:
        return False
    finally:
        unsub()
    return True


class PlayerBackend:
    """Allgemeiner Player: Abspielen ja, Fortsetzen nur von vorne."""

    supports_restore = False

    def __init__(self, hass: HomeAssistant, entity_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.options = options

    @property
    def state(self) -> State | None:
        return self.hass.states.get(self.entity_id)

    async def _call(self, domain: str, service: str, **data: Any) -> None:
        _LOGGER.debug("%s: %s.%s %s", self.entity_id, domain, service, data)
        await self.hass.services.async_call(
            domain, service, {ATTR_ENTITY_ID: self.entity_id, **data}, blocking=True
        )

    async def async_play_media(self, media: dict[str, Any]) -> None:
        await self._call(
            MP_DOMAIN,
            SERVICE_PLAY_MEDIA,
            **{
                ATTR_MEDIA_CONTENT_ID: media["media_content_id"],
                ATTR_MEDIA_CONTENT_TYPE: media["media_content_type"],
            },
        )

    async def async_play(self) -> None:
        await self._call(MP_DOMAIN, SERVICE_MEDIA_PLAY)

    async def async_pause(self) -> None:
        await self._call(MP_DOMAIN, SERVICE_MEDIA_PAUSE)

    async def async_play_pause(self) -> None:
        await self._call(MP_DOMAIN, SERVICE_MEDIA_PLAY_PAUSE)

    async def async_restart(self) -> None:
        """Medium von vorne (lange Zurück-Taste)."""
        await self.async_seek(0)

    async def async_seek(self, position: float) -> None:
        await self._call(MP_DOMAIN, SERVICE_MEDIA_SEEK, **{ATTR_MEDIA_SEEK_POSITION: position})

    def supports(self, feature: MediaPlayerEntityFeature) -> bool:
        state = self.state
        if state is None:
            return False
        return bool(int(state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0) & feature)

    async def async_set_volume(self, percent: int) -> None:
        await self._call(
            MP_DOMAIN, SERVICE_VOLUME_SET, **{ATTR_MEDIA_VOLUME_LEVEL: round(percent / 100, 2)}
        )

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._call(MP_DOMAIN, SERVICE_SHUFFLE_SET, **{ATTR_MEDIA_SHUFFLE: shuffle})

    async def async_set_repeat(self, repeat: str) -> None:
        await self._call(MP_DOMAIN, SERVICE_REPEAT_SET, **{ATTR_MEDIA_REPEAT: repeat})

    async def async_restore(self, position: Position) -> bool:
        """Nach play_media die gemerkte Stelle anfahren. True bei Erfolg."""
        _LOGGER.info(
            "%s: Fortsetzen wird nicht unterstützt, Medium startet von vorne", self.entity_id
        )
        return False


class SonosBackend(PlayerBackend):
    """Sonos: Titelsprung per sonos.play_queue (0-basiert) und Spulen per media_seek."""

    supports_restore = True

    async def async_play_queue(self, index: int) -> None:
        await self._call(SONOS_DOMAIN, SONOS_SERVICE_PLAY_QUEUE, queue_position=index)

    async def async_restart(self) -> None:
        """Anfang der Queue, nicht nur des aktuellen Titels."""
        await self.async_play_queue(0)

    async def async_restore(self, position: Position) -> bool:
        opts = self.options
        await asyncio.sleep(float(opts[OPT_RESTORE_SETTLE]))

        started = await async_wait_for_state(
            self.hass,
            self.entity_id,
            lambda s: s is not None and s.state == STATE_PLAYING and queue_position(s) == 1,
            float(opts[OPT_RESTORE_START_TIMEOUT]),
        )
        if not started:
            state = self.state
            _LOGGER.warning(
                "%s: Wiedergabe startete nicht rechtzeitig (Zustand %s, Queue %s), "
                "Fortsetzen abgebrochen",
                self.entity_id,
                state.state if state else None,
                queue_position(state),
            )
            return False

        if position.q > 1:
            await self.async_play_queue(position.q - 1)
            jumped = await async_wait_for_state(
                self.hass,
                self.entity_id,
                lambda s: queue_position(s) == position.q,
                float(opts[OPT_QUEUE_JUMP_TIMEOUT]),
            )
            if not jumped:
                _LOGGER.warning(
                    "%s: Titel %d nicht erreicht (aktuell %d), spule trotzdem",
                    self.entity_id,
                    position.q,
                    queue_position(self.state),
                )

        tolerance = float(opts[OPT_SEEK_TOLERANCE])
        attempts = int(opts[OPT_SEEK_ATTEMPTS])
        for attempt in range(1, attempts + 1):
            try:
                await self.async_seek(position.p)
            except HomeAssistantError as err:
                _LOGGER.debug("%s: Spulen fehlgeschlagen: %s", self.entity_id, err)
            await asyncio.sleep(float(opts[OPT_SEEK_RETRY_DELAY]))
            actual = current_position(self.state)
            _LOGGER.debug(
                "%s: Spulversuch %d: Soll %ds, Ist %.0fs",
                self.entity_id,
                attempt,
                position.p,
                actual,
            )
            if abs(actual - position.p) < tolerance:
                _LOGGER.info("%s: fortgesetzt bei %s", self.entity_id, fmt_position(position))
                return True
        _LOGGER.warning(
            "%s: Stelle %s nach %d Versuchen nicht erreicht",
            self.entity_id,
            fmt_position(position),
            attempts,
        )
        return False


def create_backend(hass: HomeAssistant, entity_id: str, options: dict[str, Any]) -> PlayerBackend:
    """Backend passend zur Plattform des Players wählen."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None and entry.platform == SONOS_DOMAIN:
        return SonosBackend(hass, entity_id, options)
    return PlayerBackend(hass, entity_id, options)

"""Simulierter Sonos-Player für Tests. Registriert nur Fake-Dienste in der Test-Instanz."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util


@dataclass
class FakeSonos:
    """Verhält sich für die genutzten Dienste wie ein Sonos-Player."""

    hass: HomeAssistant
    entity_id: str
    titles: list[str] = field(default_factory=lambda: ["Kapitel 1", "Kapitel 2", "Kapitel 3"])
    state: str = "idle"
    q: int = 0
    pos: float = 0.0
    updated_at: datetime | None = None
    # Sekunden bis play_media tatsächlich "playing" meldet (None = nie)
    start_delay: float | None = 0.0
    ignore_seeks: int = 0
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def publish(self) -> None:
        attrs: dict[str, Any] = {}
        if self.q:
            attrs.update(
                queue_position=self.q,
                media_title=self.titles[self.q - 1],
                media_position=self.pos,
                media_position_updated_at=self.updated_at or dt_util.utcnow(),
            )
        self.hass.states.async_set(self.entity_id, self.state, attrs)

    def set_playing(self, q: int, pos: float, seconds_ago: float = 0.0) -> None:
        self.state, self.q, self.pos = "playing", q, pos
        self.updated_at = dt_util.utcnow() - timedelta(seconds=seconds_ago)
        self.publish()

    def set_paused(self, q: int, pos: float) -> None:
        self.state, self.q, self.pos = "paused", q, pos
        self.updated_at = dt_util.utcnow()
        self.publish()

    def services(self, name: str) -> list[dict[str, Any]]:
        return [data for svc, data in self.calls if svc == name]

    @property
    def service_names(self) -> list[str]:
        return [svc for svc, _ in self.calls]

    def _now_pos(self) -> float:
        if self.state == "playing" and self.updated_at is not None:
            return self.pos + (dt_util.utcnow() - self.updated_at).total_seconds()
        return self.pos

    async def _handle(self, call: ServiceCall) -> None:
        name = f"{call.domain}.{call.service}"
        data = {k: v for k, v in call.data.items() if k != "entity_id"}
        self.calls.append((name, data))
        if name == "media_player.play_media":
            self.state, self.q, self.pos = "idle", 0, 0.0
            self.publish()
            if self.start_delay is not None:
                self.hass.loop.call_later(self.start_delay, self._started)
        elif name == "media_player.media_pause":
            self.pos, self.state = self._now_pos(), "paused"
            self.updated_at = dt_util.utcnow()
            self.publish()
        elif name == "media_player.media_play":
            self.state, self.updated_at = "playing", dt_util.utcnow()
            self.publish()
        elif name == "media_player.media_seek":
            if self.ignore_seeks > 0:
                self.ignore_seeks -= 1
                return
            self.pos, self.updated_at = float(data["seek_position"]), dt_util.utcnow()
            self.publish()
        elif name == "sonos.play_queue":
            self.set_playing(int(data["queue_position"]) + 1, 0.0)

    def _started(self) -> None:
        self.set_playing(1, 0.0)

    def register(self, platform: str = "sonos") -> FakeSonos:
        er.async_get(self.hass).async_get_or_create(
            "media_player",
            platform,
            f"fake-{self.entity_id}",
            suggested_object_id=self.entity_id.split(".", 1)[1],
        )
        for service in ("play_media", "media_pause", "media_play", "media_seek"):
            self.hass.services.async_register("media_player", service, self._handle)
        self.hass.services.async_register("sonos", "play_queue", self._handle)
        self.publish()
        return self

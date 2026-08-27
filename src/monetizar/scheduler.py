"""Fila de publicação: distribui os cortes prontos em 5 posts por dia."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .channels import Channel
from .config import PublishConfig
from .db import ClipRecord, Database

log = logging.getLogger(__name__)


@dataclass
class ScheduledPost:
    clip_id: int
    channel_slug: str
    platform: str
    publish_at: datetime
    title: str
    path: str


class Scheduler:
    def __init__(self, db: Database, publish: PublishConfig):
        self.db = db
        self.cfg = publish
        self.tz = _load_tz(publish.timezone)

    def plan(
        self,
        channel: Channel,
        *,
        days: int | None = None,
        start_day: date | None = None,
    ) -> list[ScheduledPost]:
        """Agenda os cortes prontos do canal nos próximos dias.

        Só agenda o que já existe em disco: se houver menos cortes que vagas, a
        fila fica curta em vez de reservar espaço vazio.
        """
        days = days or self.cfg.buffer_days
        clips = self.db.ready_clips(channel.slug)
        if not clips:
            log.info("Canal '%s' não tem cortes prontos para agendar", channel.slug)
            return []

        slots = self._slots(days=days, start_day=start_day)
        scheduled: list[ScheduledPost] = []

        for clip, slot in zip(clips, slots):
            for platform in channel.platforms:
                created = self.db.schedule_clip(
                    clip.id, channel.slug, platform, slot.isoformat()
                )
                if created:
                    scheduled.append(
                        ScheduledPost(
                            clip_id=clip.id,
                            channel_slug=channel.slug,
                            platform=platform,
                            publish_at=slot,
                            title=clip.title,
                            path=clip.path,
                        )
                    )

        leftover = len(clips) - len(slots)
        if leftover > 0:
            log.info(
                "Canal '%s': %d cortes ficaram fora da janela de %d dias",
                channel.slug, leftover, days,
            )
        return scheduled

    def _slots(self, *, days: int, start_day: date | None) -> list[datetime]:
        """Horários de publicação, a partir do próximo slot ainda no futuro."""
        now = datetime.now(self.tz)
        day = start_day or now.date()
        times = sorted(_parse_time(s) for s in self.cfg.slots[: self.cfg.videos_per_day])

        slots: list[datetime] = []
        for offset in range(days):
            current = day + timedelta(days=offset)
            for slot_time in times:
                moment = datetime.combine(current, slot_time, tzinfo=self.tz)
                if moment > now:
                    slots.append(moment)
        return slots

    def upcoming(self, channel_slug: str | None = None, limit: int = 20) -> list[dict]:
        return self.db.pending_schedule(channel_slug, limit)

    def coverage(self, channel: Channel) -> dict[str, int]:
        """Quantos dias de publicação já estão cobertos pela fila."""
        pending = self.db.pending_schedule(channel.slug, limit=1000)
        per_day = self.cfg.videos_per_day * max(len(channel.platforms), 1)
        return {
            "pending": len(pending),
            "ready_unscheduled": len(self.db.ready_clips(channel.slug)),
            "days_covered": len(pending) // per_day if per_day else 0,
        }


def deficit(channels: list[Channel], db: Database, publish: PublishConfig) -> dict[str, int]:
    """Quantos cortes ainda faltam por canal para cobrir o buffer configurado.

    É a pergunta que a operação de 20 canais faz todo dia: onde o estoque vai
    acabar primeiro.
    """
    needed_per_channel = publish.videos_per_day * publish.buffer_days
    out: dict[str, int] = {}
    for channel in channels:
        have = len(db.ready_clips(channel.slug)) + len(
            db.pending_schedule(channel.slug, limit=1000)
        )
        out[channel.slug] = max(needed_per_channel - have, 0)
    return out


def _parse_time(value: str) -> time:
    try:
        hours, minutes = value.strip().split(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Horário inválido em publish.slots: {value!r}") from exc


def _load_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Fuso '%s' desconhecido; usando UTC", name)
        return ZoneInfo("UTC")

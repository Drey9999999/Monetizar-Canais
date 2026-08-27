"""Definição dos canais — o que faz a operação escalar de 1 para 20."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import REPO_ROOT

DEFAULT_CHANNELS_PATH = REPO_ROOT / "config" / "channels.yaml"
EXAMPLE_CHANNELS_PATH = REPO_ROOT / "config" / "channels.example.yaml"


@dataclass
class Channel:
    """Um canal da operação, com seu nicho e suas fontes."""

    slug: str
    name: str
    niche: str = ""
    # Buscas usadas para achar material novo.
    queries: list[str] = field(default_factory=list)
    # Canais/playlists acompanhados diretamente.
    sources: list[str] = field(default_factory=list)
    # Plataformas onde esse canal publica.
    platforms: list[str] = field(default_factory=lambda: ["youtube", "tiktok"])
    hashtags: list[str] = field(default_factory=list)
    language: str = "pt"
    # Perfil de duração: "shorts" (20-35s) ou "tiktok_rewards" (60s+, único
    # formato que o Creator Rewards do TikTok remunera).
    duration_profile: str = "shorts"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.queries and not self.sources:
            raise ValueError(
                f"Canal '{self.slug}': defina ao menos uma entrada em "
                "'queries' ou 'sources', senão não há de onde tirar material"
            )
        if self.duration_profile not in {"shorts", "tiktok_rewards"}:
            raise ValueError(
                f"Canal '{self.slug}': duration_profile inválido "
                f"'{self.duration_profile}' (use 'shorts' ou 'tiktok_rewards')"
            )

    def apply_profile(self, clip_cfg: Any) -> None:
        """Ajusta a duração do corte conforme o destino do canal.

        O TikTok só paga por views em vídeos originais de 60s ou mais; o Shorts
        favorece 20-35s. Um canal que mira os dois precisa escolher, e é isso
        que o perfil decide.
        """
        if self.duration_profile == "tiktok_rewards":
            clip_cfg.min_duration = 62.0
            clip_cfg.target_duration = 75.0
            clip_cfg.max_duration = 180.0
        else:
            clip_cfg.min_duration = 15.0
            clip_cfg.target_duration = 30.0
            clip_cfg.max_duration = 59.0


def load_channels(path: str | Path | None = None) -> list[Channel]:
    """Carrega os canais do YAML, ignorando os desabilitados."""
    candidates = [Path(path)] if path else [DEFAULT_CHANNELS_PATH, EXAMPLE_CHANNELS_PATH]
    for candidate in candidates:
        if candidate.exists():
            raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            break
    else:
        raise FileNotFoundError(
            f"Nenhum arquivo de canais encontrado em: "
            f"{', '.join(str(c) for c in candidates)}"
        )

    entries = raw.get("channels") or []
    if not entries:
        raise ValueError(f"{candidate} não define nenhum canal em 'channels'")

    channels: list[Channel] = []
    seen: set[str] = set()
    for entry in entries:
        channel = Channel(**entry)
        if channel.slug in seen:
            raise ValueError(f"Slug de canal duplicado: '{channel.slug}'")
        seen.add(channel.slug)
        if channel.enabled:
            channels.append(channel)
    return channels


def find_channel(channels: list[Channel], slug: str) -> Channel:
    for channel in channels:
        if channel.slug == slug:
            return channel
    known = ", ".join(c.slug for c in channels) or "(nenhum)"
    raise KeyError(f"Canal '{slug}' não encontrado. Disponíveis: {known}")

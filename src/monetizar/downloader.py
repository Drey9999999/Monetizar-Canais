"""Busca, navegação e download de vídeos do YouTube via yt-dlp."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    """Metadados de um vídeo, sem o arquivo baixado."""

    video_id: str
    title: str
    url: str
    duration: float
    channel: str = ""
    channel_id: str = ""
    view_count: int = 0
    like_count: int = 0
    upload_date: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    language: str = ""

    @classmethod
    def from_entry(cls, entry: dict[str, Any]) -> "VideoInfo":
        vid = entry.get("id") or ""
        return cls(
            video_id=vid,
            title=entry.get("title") or "",
            url=entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
            duration=float(entry.get("duration") or 0.0),
            channel=entry.get("channel") or entry.get("uploader") or "",
            channel_id=entry.get("channel_id") or entry.get("uploader_id") or "",
            view_count=int(entry.get("view_count") or 0),
            like_count=int(entry.get("like_count") or 0),
            upload_date=entry.get("upload_date") or "",
            description=entry.get("description") or "",
            tags=list(entry.get("tags") or []),
            language=entry.get("language") or "",
        )

    @property
    def engagement_rate(self) -> float:
        """Likes por view. Proxy barato de o quanto o público reagiu ao vídeo."""
        if self.view_count <= 0:
            return 0.0
        return self.like_count / self.view_count


@dataclass
class DownloadedVideo:
    """Um vídeo já em disco, com legenda quando disponível."""

    info: VideoInfo
    video_path: Path
    subtitle_path: Path | None = None


class Downloader:
    """Camada fina sobre o yt-dlp com as opções do projeto já aplicadas."""

    def __init__(self, config: Config):
        self.config = config
        self.dl_cfg = config.download
        self.dest = Path(config.paths.downloads)
        self.dest.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ busca

    def search(self, query: str, limit: int = 10) -> list[VideoInfo]:
        """Busca no YouTube e devolve metadados, sem baixar nada."""
        return self._extract_flat(f"ytsearch{limit}:{query}", limit)

    def channel_videos(self, channel_url: str, limit: int = 20) -> list[VideoInfo]:
        """Lista os vídeos mais recentes de um canal ou playlist."""
        return self._extract_flat(channel_url, limit)

    def _extract_flat(self, target: str, limit: int) -> list[VideoInfo]:
        """Extração rasa: uma requisição para a lista toda, sem abrir cada vídeo.

        `extract_flat` mantém a busca rápida, mas devolve entradas incompletas
        (sem duration/view_count em alguns casos), então os campos ausentes são
        completados por `inspect` só para os vídeos que sobrevivem ao filtro.
        """
        opts = self._base_opts() | {
            "extract_flat": "in_playlist",
            "playlistend": limit,
            "skip_download": True,
        }
        with YoutubeDL(opts) as ydl:
            try:
                data = ydl.extract_info(target, download=False)
            except DownloadError as exc:
                log.warning("Busca falhou para %r: %s", target, exc)
                return []

        entries = data.get("entries") if isinstance(data, dict) else None
        if entries is None:
            entries = [data] if isinstance(data, dict) else []
        return [VideoInfo.from_entry(e) for e in entries if e]

    def inspect(self, url: str) -> VideoInfo | None:
        """Metadados completos de um único vídeo."""
        opts = self._base_opts() | {"skip_download": True}
        with YoutubeDL(opts) as ydl:
            try:
                data = ydl.extract_info(url, download=False)
            except DownloadError as exc:
                log.warning("Não foi possível ler %s: %s", url, exc)
                return None
        return VideoInfo.from_entry(data) if data else None

    # ----------------------------------------------------------------- filtro

    def filter_candidates(self, videos: Iterable[VideoInfo]) -> list[VideoInfo]:
        """Descarta o que não serve como fonte de corte.

        Vídeos curtos demais não têm corte bom dentro; longos demais custam caro
        para baixar e analisar. Vídeos sem duração conhecida passam e são
        checados de novo depois do `inspect`.
        """
        kept: list[VideoInfo] = []
        for v in videos:
            if v.duration and not (
                self.dl_cfg.min_duration <= v.duration <= self.dl_cfg.max_duration
            ):
                log.debug("Ignorado (duração %.0fs): %s", v.duration, v.title)
                continue
            kept.append(v)
        return kept

    # --------------------------------------------------------------- download

    def download(self, video: VideoInfo) -> DownloadedVideo | None:
        """Baixa o vídeo e a melhor legenda disponível."""
        target = self.dest / video.video_id
        target.mkdir(parents=True, exist_ok=True)

        opts = self._base_opts() | {
            "format": (
                f"bestvideo[height<={self.dl_cfg.max_height}][ext=mp4]"
                f"+bestaudio[ext=m4a]/best[height<={self.dl_cfg.max_height}]/best"
            ),
            "merge_output_format": self.dl_cfg.container,
            "outtmpl": str(target / "source.%(ext)s"),
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": self.dl_cfg.subtitle_languages,
            "subtitlesformat": "vtt",
            "sleep_interval": self.dl_cfg.sleep_interval,
            "ratelimit": _parse_rate(self.dl_cfg.rate_limit),
            "overwrites": False,
            "continuedl": True,
        }
        if self.dl_cfg.cookies_file:
            opts["cookiefile"] = self.dl_cfg.cookies_file

        with YoutubeDL(opts) as ydl:
            try:
                data = ydl.extract_info(video.url, download=True)
            except DownloadError as exc:
                log.error("Download falhou (%s): %s", video.video_id, exc)
                return None

        video_path = _find_media(target)
        if not video_path:
            log.error("Download terminou sem arquivo de vídeo em %s", target)
            return None

        # Metadados completos só existem após o download real; a entrada rasa
        # da busca costuma vir sem duration/description.
        info = VideoInfo.from_entry(data) if data else video
        info.url = video.url
        return DownloadedVideo(
            info=info,
            video_path=video_path,
            subtitle_path=self._pick_subtitle(target),
        )

    def _pick_subtitle(self, folder: Path) -> Path | None:
        """Escolhe a legenda na ordem de preferência de idioma configurada."""
        subs = list(folder.glob("*.vtt"))
        if not subs:
            return None
        for lang in self.dl_cfg.subtitle_languages:
            for sub in subs:
                # yt-dlp nomeia como "source.pt.vtt" / "source.pt-BR.vtt".
                if f".{lang}." in sub.name:
                    return sub
        return subs[0]

    def _base_opts(self) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "ignoreerrors": True,
            "retries": 5,
            "fragment_retries": 5,
            # O yt-dlp respeita HTTPS_PROXY do ambiente por padrão.
        }


def _find_media(folder: Path) -> Path | None:
    """Localiza o arquivo de vídeo baixado, seja qual for o container final."""
    for ext in ("mp4", "mkv", "webm", "mov"):
        matches = sorted(folder.glob(f"source*.{ext}"))
        if matches:
            return matches[0]
    return None


def _parse_rate(value: str | None) -> int | None:
    """Converte "5M"/"500K" no número de bytes/s que o yt-dlp espera."""
    if not value:
        return None
    text = str(value).strip().upper()
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
    if text and text[-1] in multipliers:
        return int(float(text[:-1]) * multipliers[text[-1]])
    return int(float(text))

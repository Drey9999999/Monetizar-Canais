"""Busca, navegação e download de vídeos do YouTube via yt-dlp."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .config import Config

log = logging.getLogger(__name__)


class DownloaderError(RuntimeError):
    """Falha ao falar com o YouTube."""


class ExtractionError(DownloaderError):
    """Falha transitória que sobreviveu a todas as tentativas.

    Rate limit, 403 na API, checagem de bot. Distinguir isso de "não achei
    nada" é o ponto: sem essa separação uma busca bloqueada aparece na tela
    exatamente igual a uma busca sem resultado.
    """


class SourceUnavailable(DownloaderError):
    """O vídeo existe mas não pode ser baixado — e tentar de novo não resolve.

    Restrição de idade, vídeo privado, removido, bloqueado por região ou
    exclusivo para membros. `reason` diz qual, para o relatório do run.
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# Marcadores de erro que valem uma nova tentativa: são de momento, não do vídeo.
TRANSIENT_MARKERS = (
    "http error 429",
    "too many requests",
    "http error 403",
    "unable to download api page",
    "sign in to confirm you're not a bot",
    "sign in to confirm you’re not a bot",
    "failed to extract any player response",
    "unable to download webpage",
    "read timed out",
    "connection reset",
    "temporary failure in name resolution",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
)

# Marcadores que descrevem o vídeo, não a rede. Repetir não muda o resultado.
PERMANENT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("idade", ("confirm your age", "age-restricted", "age restricted",
               "inappropriate for some users")),
    ("privado", ("private video", "this video is private")),
    ("membros", ("members-only", "join this channel")),
    ("regiao", ("not available in your country", "geo restricted",
                "geo-restricted", "blocked it in your country",
                "not made this video available in your country")),
    ("removido", ("video unavailable", "has been removed", "no longer available",
                  "does not exist", "account associated with this video has been "
                  "terminated")),
    ("ao_vivo", ("is live", "premieres in", "this live event will begin")),
)


def classify_error(exc: BaseException) -> str | None:
    """Devolve o motivo permanente do erro, ou None se vale tentar de novo.

    A ordem importa: um vídeo com restrição de idade também responde 403, e
    tratar isso como rate limit gastaria as tentativas à toa.
    """
    text = str(exc).lower()
    for reason, markers in PERMANENT_MARKERS:
        if any(marker in text for marker in markers):
            return reason
    if any(marker in text for marker in TRANSIENT_MARKERS):
        return None
    # Erro desconhecido: trata como permanente para não repetir sem motivo.
    return "desconhecido"


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
    license: str = ""

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
            license=entry.get("license") or "",
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

        Um erro aqui sobe como exceção em vez de virar lista vazia: quem chama
        precisa saber a diferença entre "a busca não achou nada" e "o YouTube
        recusou a requisição".
        """
        opts = self._base_opts() | {
            "extract_flat": "in_playlist",
            "playlistend": limit,
            "skip_download": True,
        }
        data = self._extract(opts, target)

        entries = data.get("entries") if isinstance(data, dict) else None
        if entries is None:
            entries = [data] if isinstance(data, dict) else []
        return [VideoInfo.from_entry(e) for e in entries if e]

    def inspect(self, url: str) -> VideoInfo | None:
        """Metadados completos de um único vídeo."""
        opts = self._base_opts() | {"skip_download": True}
        try:
            data = self._extract(opts, url)
        except SourceUnavailable as exc:
            log.warning("Não foi possível ler %s (%s)", url, exc.reason)
            return None
        return VideoInfo.from_entry(data) if data else None

    # ---------------------------------------------------------------- retries

    def _extract(
        self, opts: dict[str, Any], target: str, *, download: bool = False
    ) -> dict[str, Any] | None:
        """Chama o yt-dlp repetindo só o que vale a pena repetir.

        `retries` do próprio yt-dlp cobre o download dos fragmentos, não a
        extração: um 403 na página de API estoura antes de qualquer retry
        interno acontecer. Este laço é o que cobre esse caso.
        """
        attempts = max(int(self.dl_cfg.extract_retries), 1)
        delay = max(float(self.dl_cfg.retry_backoff), 0.0)
        last: BaseException | None = None

        for attempt in range(1, attempts + 1):
            try:
                with YoutubeDL(opts) as ydl:
                    return ydl.extract_info(target, download=download)
            except DownloadError as exc:
                reason = classify_error(exc)
                if reason is not None:
                    raise SourceUnavailable(reason, _first_line(exc)) from exc
                last = exc
                if attempt < attempts:
                    log.info(
                        "Tentativa %d/%d falhou (%s); repetindo em %.0fs",
                        attempt, attempts, _first_line(exc)[:90], delay,
                    )
                    time.sleep(delay)
                    # Teto na espera: dobrar sem limite faz a 10ª tentativa
                    # dormir mais de 20 minutos.
                    delay = min(delay * 2, max(float(self.dl_cfg.retry_backoff_max), 0.0))

        raise ExtractionError(
            f"YouTube recusou {attempts} tentativas para {target!r}: "
            f"{_first_line(last) if last else 'sem detalhe'}"
        )

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
        """Baixa o vídeo e a melhor legenda disponível.

        Levanta `SourceUnavailable` quando o vídeo em si é o problema (idade,
        privado, região) e `ExtractionError` quando o YouTube recusou a
        conversa. Devolve None só quando o download terminou sem arquivo.
        """
        target = self.dest / video.video_id
        target.mkdir(parents=True, exist_ok=True)

        opts = self._base_opts() | {
            "format": self._format_selector(),
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

        data = self._extract(opts, video.url, download=True)

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

    def _format_selector(self) -> str:
        """Seletor de formato com degradação em degraus.

        O par mp4+m4a é o ideal (nada para recodificar), mas nem todo vídeo
        tem os dois. Sem o degrau intermediário de vídeo adaptativo em
        qualquer codec, a queda vai direto para `best`, que no YouTube é
        progressivo e trava em 360p — o corte sai borrado sem nenhum aviso.
        """
        h = self.dl_cfg.max_height
        return (
            f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={h}]+bestaudio/"
            f"best[height<={h}]/"
            f"bestvideo+bestaudio/best"
        )

    def _pick_subtitle(self, folder: Path) -> Path | None:
        """Escolhe a legenda na ordem de preferência de idioma configurada.

        A comparação é por prefixo de tag: `pt` cobre `pt`, `pt-BR` e o
        `pt-orig` das legendas automáticas. Se nenhum idioma configurado
        aparecer, devolve None em vez de um arquivo qualquer — legenda em
        idioma errado queimada no vídeo é pior que corte sem legenda.
        """
        subs = list(folder.glob("*.vtt"))
        if not subs:
            return None

        for lang in self.dl_cfg.subtitle_languages:
            wanted = lang.lower()
            # Nome mais curto primeiro: "source.pt.vtt" ganha de "source.pt-BR.vtt".
            for sub in sorted(subs, key=lambda p: (len(p.name), p.name)):
                tag = _lang_tag(sub)
                if tag == wanted or tag.startswith(f"{wanted}-"):
                    return sub

        log.warning(
            "Nenhuma legenda em %s; disponíveis: %s. Seguindo sem legenda.",
            "/".join(self.dl_cfg.subtitle_languages),
            ", ".join(sorted(_lang_tag(s) for s in subs)) or "nenhuma",
        )
        return None

    def _base_opts(self) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            # ignoreerrors=True fazia o yt-dlp engolir a falha e devolver None,
            # o que chegava aqui como "nenhum resultado". Os erros agora sobem
            # e quem chama decide o que fazer com cada um.
            "ignoreerrors": False,
            "retries": 5,
            "fragment_retries": 5,
            # O yt-dlp respeita HTTPS_PROXY do ambiente por padrão.
        }


# yt-dlp deixa "source.f137.mp4" para trás quando baixa vídeo e áudio separados
# e o merge não termina.
FORMAT_FRAGMENT_RE = re.compile(r"\.f\d+$")

MEDIA_EXTS = ("mp4", "mkv", "webm", "mov")


def _find_media(folder: Path) -> Path | None:
    """Localiza o arquivo de vídeo baixado, seja qual for o container final.

    O arquivo com nome exato vem primeiro. Os restos por-formato do download
    adaptativo são ignorados: "source.f137.mp4" ordena antes de "source.mp4"
    em ordem alfabética, então pegar o primeiro do glob devolvia o vídeo sem
    faixa de áudio — e o corte saía mudo.
    """
    for ext in MEDIA_EXTS:
        exact = folder / f"source.{ext}"
        if exact.exists():
            return exact
    for ext in MEDIA_EXTS:
        matches = sorted(
            p for p in folder.glob(f"source*.{ext}")
            if not FORMAT_FRAGMENT_RE.search(p.stem)
        )
        if matches:
            return matches[0]
    return None


def _lang_tag(path: Path) -> str:
    """Tag de idioma no nome do arquivo: "source.pt-BR.vtt" -> "pt-br"."""
    parts = path.name.split(".")
    return parts[-2].lower() if len(parts) >= 3 else ""


def _first_line(exc: BaseException | None) -> str:
    return str(exc).strip().split("\n")[0] if exc else ""


def _parse_rate(value: str | None) -> int | None:
    """Converte "5M"/"500K" no número de bytes/s que o yt-dlp espera.

    Valor malformado vira None (sem limite) com aviso: derrubar o run inteiro
    por causa de um campo opcional de config é pior que ignorá-lo.
    """
    if not value:
        return None
    text = str(value).strip().upper()
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
    try:
        if text and text[-1] in multipliers:
            return int(float(text[:-1]) * multipliers[text[-1]])
        return int(float(text))
    except ValueError:
        log.warning("download.rate_limit inválido (%r); seguindo sem limite", value)
        return None

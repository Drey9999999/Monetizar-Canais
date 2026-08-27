"""Orquestração: da busca no YouTube ao corte vertical pronto para postar."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .analyzer import Analyzer, Segment
from .captions import Cue, parse_vtt
from .channels import Channel
from .config import Config
from .db import Database
from .downloader import (
    DownloadedVideo,
    Downloader,
    DownloaderError,
    SourceUnavailable,
    VideoInfo,
)
from .editor import Editor, EditorError
from .metadata import ClipMetadata, build_metadata

log = logging.getLogger(__name__)


@dataclass
class ClipOutput:
    """Um corte renderizado, com tudo que a publicação precisa."""

    clip_id: int
    path: Path
    source_id: str
    source_url: str
    start: float
    end: float
    score: float
    metadata: ClipMetadata

    def to_dict(self) -> dict:
        return {
            "clip_id": self.clip_id,
            "path": str(self.path),
            "source_id": self.source_id,
            "source_url": self.source_url,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "score": round(self.score, 4),
            **asdict(self.metadata),
        }


@dataclass
class RunReport:
    channel: str
    discovered: int = 0
    downloaded: int = 0
    skipped: int = 0
    clips: list[ClipOutput] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def clip_count(self) -> int:
        return len(self.clips)


class Pipeline:
    def __init__(self, config: Config, db: Database | None = None):
        self.config = config
        config.paths.ensure_dirs()
        self.db = db or Database(config.paths.database)
        self.downloader = Downloader(config)
        self.analyzer = Analyzer(config.analysis, config.clip)
        self.editor = Editor(config.clip, Path(config.paths.work))

    # ------------------------------------------------------------- descoberta

    def discover(
        self, channel: Channel, per_query: int = 10, report: RunReport | None = None
    ) -> list[VideoInfo]:
        """Junta candidatos das buscas e das fontes fixas do canal.

        Uma busca que falha não derruba as outras, mas também não some: fica
        registrada em `report.errors`, senão um bloqueio do YouTube chega na
        tela como "nenhum vídeo encontrado".
        """
        found: dict[str, VideoInfo] = {}

        for query in channel.queries:
            try:
                videos = self.downloader.search(query, limit=per_query)
            except DownloaderError as exc:
                log.warning("Busca %r falhou: %s", query, exc)
                if report is not None:
                    report.errors.append(f"busca {query!r}: {exc}")
                continue
            for video in videos:
                found.setdefault(video.video_id, video)

        for source in channel.sources:
            try:
                videos = self.downloader.channel_videos(source, limit=per_query)
            except DownloaderError as exc:
                log.warning("Fonte %s falhou: %s", source, exc)
                if report is not None:
                    report.errors.append(f"fonte {source}: {exc}")
                continue
            for video in videos:
                found.setdefault(video.video_id, video)

        candidates = self.downloader.filter_candidates(found.values())
        # Vídeo já usado como fonte não volta: dois cortes do mesmo material em
        # canais diferentes é o padrão que a detecção de conteúdo repetitivo pega.
        fresh = [v for v in candidates if not self.db.has_source(v.video_id)]

        # Mais engajamento primeiro: se o público reagiu ao vídeo inteiro, é mais
        # provável que exista um momento forte dentro dele.
        fresh.sort(key=lambda v: (v.engagement_rate, v.view_count), reverse=True)
        return fresh

    # ----------------------------------------------------------------- execução

    def run(
        self,
        channel: Channel,
        *,
        max_sources: int = 3,
        max_clips: int | None = None,
        per_query: int = 10,
        dry_run: bool = False,
    ) -> RunReport:
        """Ciclo completo para um canal: descobrir, baixar, cortar, registrar."""
        channel.apply_profile(self.config.clip)
        report = RunReport(channel=channel.slug)

        candidates = self.discover(channel, per_query=per_query, report=report)
        report.discovered = len(candidates)
        log.info("Canal '%s': %d fontes candidatas", channel.slug, len(candidates))

        if dry_run:
            for video in candidates[:max_sources]:
                log.info(
                    "  [dry-run] %s (%.0fs, %d views) — %s",
                    video.video_id, video.duration, video.view_count, video.title,
                )
            return report

        target_clips = max_clips or self.config.clip.max_clips_per_source * max_sources

        for video in candidates:
            if len(report.clips) >= target_clips or report.downloaded >= max_sources:
                break
            try:
                produced = self._process_source(channel, video, report)
            except Exception as exc:  # uma fonte ruim não pode derrubar o lote
                log.exception("Erro processando %s", video.video_id)
                report.errors.append(f"{video.video_id}: {exc}")
                # Upsert: a fonte pode ter falhado antes de ser inserida.
                self.db.add_source(video, status="error")
                continue
            report.clips.extend(produced[: max(target_clips - len(report.clips), 0)])

        self._write_manifest(channel, report)
        return report

    def _process_source(
        self, channel: Channel, video: VideoInfo, report: RunReport
    ) -> list[ClipOutput]:
        log.info("Baixando %s — %s", video.video_id, video.title[:70])
        try:
            downloaded = self.downloader.download(video)
        except SourceUnavailable as exc:
            # O vídeo é o problema (idade, privado, região): registrar o motivo
            # e seguir. Tentar de novo depois não muda nada.
            log.info("  fonte indisponível (%s), pulando", exc.reason)
            # add_source (upsert) e não mark_source (UPDATE): a fonte nunca foi
            # inserida, então um UPDATE não gravaria nada e o vídeo voltaria na
            # descoberta do próximo run, para falhar de novo pelo mesmo motivo.
            self.db.add_source(video, status=f"unavailable:{exc.reason}")
            report.errors.append(f"{video.video_id}: indisponível ({exc.reason})")
            report.skipped += 1
            return []

        if not downloaded:
            report.skipped += 1
            return []

        report.downloaded += 1
        info = downloaded.info
        self.db.add_source(info)

        # Revalida a duração: a busca rasa às vezes vem sem esse campo, e só
        # aqui dá para saber se o vídeo realmente serve.
        if not (
            self.config.download.min_duration
            <= info.duration
            <= self.config.download.max_duration
        ):
            log.info("  fonte fora da faixa de duração (%.0fs), pulando", info.duration)
            self.db.mark_source(info.video_id, "skipped")
            report.skipped += 1
            return []

        cues = self._load_cues(downloaded)
        segments = self.analyzer.find_segments(
            downloaded.video_path, cues, info.duration
        )
        if not segments:
            log.info("  nenhum trecho bom encontrado")
            self.db.mark_source(info.video_id, "no_segments")
            return []

        outputs: list[ClipOutput] = []
        for index, segment in enumerate(segments, start=1):
            clip = self._render_clip(channel, downloaded, segment, cues, index)
            if clip:
                outputs.append(clip)
            else:
                report.errors.append(
                    f"{info.video_id}#{index}: falha ao renderizar"
                )

        self.db.mark_source(info.video_id, "processed")
        return outputs

    def _render_clip(
        self,
        channel: Channel,
        downloaded: DownloadedVideo,
        segment: Segment,
        cues: list[Cue],
        index: int,
    ) -> ClipOutput | None:
        info = downloaded.info
        meta = build_metadata(
            segment,
            info,
            channel_name=channel.name,
            base_hashtags=channel.hashtags,
        )

        out_dir = Path(self.config.paths.clips) / channel.slug
        out_path = out_dir / f"{info.video_id}_{index:02d}_{_slug(meta.title)}.mp4"

        if self.db.clip_exists(str(out_path)):
            log.info("  corte %d já existe, pulando", index)
            return None

        try:
            self.editor.render(
                downloaded.video_path,
                segment,
                out_path,
                cues=cues,
                hook=meta.hook,
            )
        except EditorError as exc:
            log.error("  %s", exc)
            return None

        clip_id = self.db.add_clip(
            source_id=info.video_id,
            channel_slug=channel.slug,
            path=str(out_path),
            start_s=segment.start,
            end_s=segment.end,
            score=segment.score,
            title=meta.title,
            description=meta.description,
            hashtags=meta.hashtags,
            hook=meta.hook,
        )

        log.info(
            "  corte %d pronto: %.1fs-%.1fs (nota %.2f) -> %s",
            index, segment.start, segment.end, segment.score, out_path.name,
        )
        return ClipOutput(
            clip_id=clip_id,
            path=out_path,
            source_id=info.video_id,
            source_url=info.url,
            start=segment.start,
            end=segment.end,
            score=segment.score,
            metadata=meta,
        )

    # ---------------------------------------------------------------- auxiliares

    def _load_cues(self, downloaded: DownloadedVideo) -> list[Cue]:
        if not downloaded.subtitle_path or not downloaded.subtitle_path.exists():
            log.info("  sem legenda: análise usará só a energia do áudio")
            return []
        try:
            cues = parse_vtt(downloaded.subtitle_path)
            com_timing = sum(1 for c in cues if c.words)
            log.info(
                "  legenda %s: %d linhas, %d com timing de palavra",
                downloaded.subtitle_path.name, len(cues), com_timing,
            )
            if self.config.clip.caption_style == "karaoke" and not com_timing:
                # Legenda manual do YouTube não traz <00:00:01.240> por palavra;
                # só a automática traz. Sem isso o karaokê vira legenda em bloco
                # — o corte sai, mas sem o efeito palavra a palavra.
                log.warning(
                    "  legenda sem timing de palavra (provavelmente manual); "
                    "o estilo karaokê vai sair como bloco"
                )
            return cues
        except Exception as exc:
            log.warning("  legenda ilegível (%s); seguindo sem ela", exc)
            return []

    def _write_manifest(self, channel: Channel, report: RunReport) -> None:
        """Manifesto por canal — é o que se lê antes de aprovar e postar."""
        if not report.clips:
            return
        manifest = Path(self.config.paths.clips) / channel.slug / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)

        existing: list[dict] = []
        if manifest.exists():
            try:
                existing = json.loads(manifest.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("manifest.json corrompido em %s; recriando", manifest.parent)

        existing.extend(clip.to_dict() for clip in report.clips)
        manifest.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^0-9A-Za-zÀ-ÿ]+", "-", text.lower()).strip("-")
    return slug[:limit].strip("-") or "corte"

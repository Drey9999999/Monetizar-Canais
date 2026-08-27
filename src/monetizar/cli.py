"""Interface de linha de comando do projeto."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .analyzer import Analyzer
from .captions import parse_vtt
from .channels import Channel, find_channel, load_channels
from .config import Config
from .db import Database
from .downloader import Downloader, DownloaderError
from .editor import Editor, EditorError
from .metadata import build_metadata
from .pipeline import Pipeline, RunReport
from .scheduler import Scheduler, deficit


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname).1s %(message)s",
    )

    try:
        return args.func(args)
    except (FileNotFoundError, KeyError, ValueError, EditorError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrompido", file=sys.stderr)
        return 130


# ------------------------------------------------------------------- comandos


def cmd_search(args: argparse.Namespace) -> int:
    """Navega o YouTube sem baixar nada."""
    config = Config.load(args.config)
    downloader = Downloader(config)

    try:
        videos = (
            downloader.channel_videos(args.query, limit=args.limit)
            if args.query.startswith("http")
            else downloader.search(args.query, limit=args.limit)
        )
    except DownloaderError as exc:
        # Sem isto, um bloqueio do YouTube saía como "nenhum vídeo encontrado".
        print(f"erro: a busca não chegou ao YouTube — {exc}", file=sys.stderr)
        return 2
    videos = downloader.filter_candidates(videos)

    if args.json:
        print(json.dumps([v.__dict__ for v in videos], ensure_ascii=False, indent=2))
        return 0

    if not videos:
        print("Nenhum vídeo dentro dos filtros de duração.")
        return 0

    for video in videos:
        mins, secs = divmod(int(video.duration), 60)
        print(
            f"{video.video_id}  {mins:3d}:{secs:02d}  "
            f"{video.view_count:>12,}v  {video.engagement_rate:.2%}  {video.title[:60]}"
        )
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    """Baixa um vídeo específico com legenda."""
    config = Config.load(args.config)
    downloader = Downloader(config)

    try:
        info = downloader.inspect(args.url)
        if not info:
            print("erro: não foi possível ler o vídeo", file=sys.stderr)
            return 1

        result = downloader.download(info)
    except DownloaderError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2

    if not result:
        print("erro: download falhou", file=sys.stderr)
        return 1

    print(f"vídeo:   {result.video_path}")
    print(f"legenda: {result.subtitle_path or '(nenhuma disponível)'}")
    return 0


def cmd_clip(args: argparse.Namespace) -> int:
    """Corta um arquivo local já baixado — útil para testar a edição."""
    config = Config.load(args.config)
    source = Path(args.video)
    if not source.exists():
        raise FileNotFoundError(f"Vídeo não encontrado: {source}")

    cues = parse_vtt(Path(args.subtitles)) if args.subtitles else []

    from .analyzer import Segment
    from .editor import probe_duration

    duration = probe_duration(source) or 0.0
    if args.start is not None:
        end = args.end if args.end is not None else args.start + config.clip.target_duration
        segments = [Segment(start=args.start, end=min(end, duration), score=1.0)]
        if cues:
            from .captions import text_between
            segments[0].text = text_between(cues, segments[0].start, segments[0].end)
    else:
        analyzer = Analyzer(config.analysis, config.clip)
        segments = analyzer.find_segments(source, cues, duration, limit=args.count)

    if not segments:
        print("Nenhum trecho encontrado.")
        return 1

    editor = Editor(config.clip, Path(config.paths.work))
    out_dir = Path(args.output or Path(config.paths.clips) / "manual")
    out_dir.mkdir(parents=True, exist_ok=True)

    from .downloader import VideoInfo

    stub = VideoInfo(video_id=source.stem, title=source.stem, url="", duration=duration)
    for index, segment in enumerate(segments, start=1):
        meta = build_metadata(segment, stub, credit=False)
        out = out_dir / f"{source.stem}_{index:02d}.mp4"
        editor.render(source, segment, out, cues=cues, hook=meta.hook)
        print(f"{out}  ({segment.start:.1f}s-{segment.end:.1f}s)  gancho: {meta.hook}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Pipeline completo para um canal ou para todos."""
    config = Config.load(args.config)
    channels = load_channels(args.channels)
    targets = [find_channel(channels, args.channel)] if args.channel else channels

    pipeline = Pipeline(config)
    reports: list[RunReport] = []

    for channel in targets:
        print(f"\n=== {channel.slug} ({channel.niche or 'sem nicho'}) ===")
        report = pipeline.run(
            channel,
            max_sources=args.max_sources,
            max_clips=args.max_clips,
            per_query=args.per_query,
            dry_run=args.dry_run,
        )
        reports.append(report)
        print(
            f"fontes: {report.discovered} encontradas, {report.downloaded} baixadas, "
            f"{report.skipped} puladas | cortes: {report.clip_count}"
        )
        for error in report.errors:
            print(f"  ! {error}")

    total = sum(r.clip_count for r in reports)
    print(f"\ntotal: {total} cortes gerados em {len(reports)} canal(is)")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    """Distribui os cortes prontos na grade de publicação."""
    config = Config.load(args.config)
    channels = load_channels(args.channels)
    targets = [find_channel(channels, args.channel)] if args.channel else channels

    db = Database(config.paths.database)
    scheduler = Scheduler(db, config.publish)

    for channel in targets:
        posts = scheduler.plan(channel, days=args.days)
        print(f"\n=== {channel.slug} ===")
        if not posts:
            print("  nada novo para agendar")
        for post in posts:
            when = post.publish_at.strftime("%d/%m %H:%M")
            print(f"  {when}  [{post.platform:8}]  {post.title[:60]}")

        cov = scheduler.coverage(channel)
        print(
            f"  fila: {cov['pending']} posts ({cov['days_covered']} dias) | "
            f"prontos sem agenda: {cov['ready_unscheduled']}"
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Panorama da operação: estoque, fila e o que falta produzir."""
    config = Config.load(args.config)
    db = Database(config.paths.database)

    stats = db.stats()
    print("--- geral ---")
    for key, value in stats.items():
        print(f"  {key:10} {value}")

    try:
        channels = load_channels(args.channels)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n(canais não carregados: {exc})")
        return 0

    gaps = deficit(channels, db, config.publish)
    scheduler = Scheduler(db, config.publish)

    print(f"\n--- canais ({len(channels)} ativos) ---")
    need_total = 0
    for channel in channels:
        cov = scheduler.coverage(channel)
        missing = gaps[channel.slug]
        need_total += missing
        flag = "OK " if missing == 0 else "!! "
        print(
            f"  {flag}{channel.slug:<20} fila {cov['pending']:>3} "
            f"({cov['days_covered']}d)  prontos {cov['ready_unscheduled']:>3}  "
            f"faltam {missing:>3}"
        )

    per_day = config.publish.videos_per_day
    print(
        f"\nmeta: {per_day}/dia por canal x {len(channels)} canais = "
        f"{per_day * len(channels)} vídeos/dia"
    )
    if need_total:
        print(f"déficit total para cobrir {config.publish.buffer_days} dias: {need_total} cortes")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    """Lista o que está agendado, pronto para subir manualmente ou via API."""
    config = Config.load(args.config)
    db = Database(config.paths.database)
    pending = db.pending_schedule(args.channel, limit=args.limit)

    if args.json:
        print(json.dumps(pending, ensure_ascii=False, indent=2))
        return 0

    if not pending:
        print("Fila vazia. Rode 'monetizar run' e depois 'monetizar schedule'.")
        return 0

    for item in pending:
        print(f"\n[{item['publish_at']}] {item['channel_slug']} -> {item['platform']}")
        print(f"  arquivo: {item['path']}")
        print(f"  título:  {item['title']}")
        print(f"  gancho:  {item['hook']}")
        print(f"  tags:    {' '.join(json.loads(item['hashtags'] or '[]'))}")
    return 0


def cmd_channels(args: argparse.Namespace) -> int:
    """Valida e lista o arquivo de canais."""
    channels = load_channels(args.channels)
    for channel in channels:
        print(
            f"{channel.slug:<20} {channel.duration_profile:<15} "
            f"{','.join(channel.platforms):<18} "
            f"{len(channel.queries)} buscas, {len(channel.sources)} fontes"
        )
    print(f"\n{len(channels)} canal(is) ativo(s)")
    return 0


# -------------------------------------------------------------------- parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monetizar",
        description="Pipeline de cortes verticais para YouTube Shorts e TikTok.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    parser.add_argument("--config", help="caminho do config.yaml")
    parser.add_argument("--channels", help="caminho do channels.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="buscar vídeos no YouTube (sem baixar)")
    p.add_argument("query", help="termo de busca ou URL de canal/playlist")
    p.add_argument("-n", "--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("download", help="baixar um vídeo com legenda")
    p.add_argument("url")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("clip", help="cortar um arquivo local")
    p.add_argument("video", help="caminho do vídeo")
    p.add_argument("--subtitles", help="caminho do .vtt")
    p.add_argument("--start", type=float, help="início manual em segundos")
    p.add_argument("--end", type=float, help="fim manual em segundos")
    p.add_argument("-c", "--count", type=int, default=3, help="quantos cortes automáticos")
    p.add_argument("-o", "--output", help="diretório de saída")
    p.set_defaults(func=cmd_clip)

    p = sub.add_parser("run", help="pipeline completo por canal")
    p.add_argument("-c", "--channel", help="slug do canal (padrão: todos)")
    p.add_argument("--max-sources", type=int, default=3, help="vídeos a baixar por canal")
    p.add_argument("--max-clips", type=int, help="teto de cortes por execução")
    p.add_argument("--per-query", type=int, default=10, help="resultados por busca")
    p.add_argument("--dry-run", action="store_true", help="só listar candidatos")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("schedule", help="montar a grade de publicação")
    p.add_argument("-c", "--channel", help="slug do canal (padrão: todos)")
    p.add_argument("-d", "--days", type=int, help="dias a preencher")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("status", help="panorama do estoque e da fila")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("queue", help="listar posts agendados")
    p.add_argument("-c", "--channel")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("channels", help="validar e listar os canais")
    p.set_defaults(func=cmd_channels)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())

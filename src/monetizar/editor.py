"""Edição dos cortes com ffmpeg: recorte, vertical 9:16, legenda e áudio."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .analyzer import Segment
from .captions import Cue, build_ass, slice_cues
from .config import ClipConfig

log = logging.getLogger(__name__)


class EditorError(RuntimeError):
    """ffmpeg falhou ao renderizar um corte."""


@dataclass
class RenderResult:
    path: Path
    duration: float
    width: int
    height: int


class Editor:
    def __init__(self, clip: ClipConfig, work_dir: Path):
        self.cfg = clip
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        _require_binary("ffmpeg")

    def render(
        self,
        source: Path,
        segment: Segment,
        output: Path,
        *,
        cues: list[Cue] | None = None,
        hook: str | None = None,
    ) -> RenderResult:
        """Renderiza um corte vertical pronto para Shorts/TikTok."""
        output.parent.mkdir(parents=True, exist_ok=True)
        cfg = self.cfg

        ass_path: Path | None = None
        if cfg.burn_captions and (cues or hook):
            ass_path = self._write_ass(segment, cues or [], hook, output.stem)

        filter_chain = self._build_video_filter(ass_path)

        cmd = [
            "ffmpeg", "-nostdin", "-y", "-v", "error",
            # -ss antes de -i faz busca rápida por keyframe; -accurate_seek
            # traz o corte de volta para o instante exato pedido.
            "-accurate_seek",
            "-ss", f"{segment.start:.3f}",
            "-t", f"{segment.duration:.3f}",
            "-i", str(source),
            "-filter_complex", filter_chain,
            "-map", "[vout]",
            "-map", "0:a?",
            "-r", str(cfg.fps),
            "-c:v", "libx264",
            "-preset", cfg.preset,
            "-crf", str(cfg.crf),
            "-maxrate", cfg.video_bitrate,
            "-bufsize", _double_rate(cfg.video_bitrate),
            "-pix_fmt", "yuv420p",
            # faststart põe o índice no começo do arquivo: o upload processa
            # antes e o preview carrega sem baixar o vídeo inteiro.
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", cfg.audio_bitrate,
            "-ar", "48000",
        ]
        if cfg.normalize_audio:
            # -14 LUFS é o alvo que YouTube e TikTok usam ao normalizar; entregar
            # já nesse nível evita a plataforma abaixar o volume do corte.
            cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
        cmd.append(str(output))

        result = subprocess.run(cmd, capture_output=True, text=True)
        if ass_path and ass_path.exists():
            ass_path.unlink()
        if result.returncode != 0:
            raise EditorError(
                f"ffmpeg falhou no corte {segment.start:.1f}-{segment.end:.1f}s "
                f"de {source.name}: {result.stderr.strip()[-800:]}"
            )
        if not output.exists() or output.stat().st_size == 0:
            raise EditorError(f"ffmpeg terminou sem gerar {output}")

        return RenderResult(
            path=output,
            duration=probe_duration(output) or segment.duration,
            width=cfg.width,
            height=cfg.height,
        )

    # ---------------------------------------------------------------- filtros

    def _build_video_filter(self, ass_path: Path | None) -> str:
        cfg = self.cfg
        w, h = cfg.width, cfg.height

        if cfg.reframe == "blur":
            # Fundo: o próprio vídeo ampliado até cobrir e desfocado; frente: o
            # vídeo inteiro, sem perder as bordas do enquadramento original.
            chain = (
                f"[0:v]split=2[bg][fg];"
                f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},gblur=sigma=28,eq=brightness=-0.12[bgb];"
                f"[fg]scale={w}:-2:force_original_aspect_ratio=decrease[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[base]"
            )
        elif cfg.reframe == "crop":
            # Corte central: preenche a tela toda, mas descarta as laterais.
            chain = (
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}[base]"
            )
        else:  # pad
            chain = (
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[base]"
            )

        if ass_path:
            chain += f";[base]subtitles='{_escape_path(ass_path)}'[vout]"
        else:
            chain += ";[base]null[vout]"
        return chain

    def _write_ass(
        self, segment: Segment, cues: list[Cue], hook: str | None, stem: str
    ) -> Path:
        local = slice_cues(cues, segment.start, segment.end)
        content = build_ass(
            local,
            width=self.cfg.width,
            height=self.cfg.height,
            style=self.cfg.caption_style,
            hook=hook if self.cfg.hook_enabled else None,
            hook_duration=self.cfg.hook_duration,
        )
        path = self.work_dir / f"{stem}.ass"
        path.write_text(content, encoding="utf-8")
        return path


# ------------------------------------------------------------------ utilidades


def probe_duration(path: Path) -> float | None:
    """Duração real de um arquivo, via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        return float(out.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise EditorError(
            f"{name} não encontrado no PATH. Instale com: sudo apt install ffmpeg"
        )


def _escape_path(path: Path) -> str:
    """Escapa o caminho para o filtro `subtitles`, que reparseia a string."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _double_rate(rate: str) -> str:
    """bufsize = 2x maxrate, a recomendação padrão do x264."""
    text = rate.strip().upper()
    if text and text[-1] in "KMG":
        return f"{float(text[:-1]) * 2:g}{text[-1]}"
    return str(int(float(text) * 2))

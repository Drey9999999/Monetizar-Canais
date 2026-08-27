"""Escolha dos melhores momentos de um vídeo para virar corte.

A nota de cada trecho combina três sinais baratos de calcular:

* energia do áudio — picos de volume marcam ênfase, risada, reação;
* palavras de gancho na legenda — "o segredo", "ninguém te conta", "o erro";
* densidade de fala — trecho com muita palavra por segundo prende mais que
  silêncio ou música de fundo.

Nada disso entende o conteúdo como um humano entenderia, então o resultado é
uma lista de candidatos ordenada, não um veredito. O `pipeline` corta os
melhores e deixa o resto registrado para revisão.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .captions import Cue, text_between
from .config import AnalysisConfig, ClipConfig

log = logging.getLogger(__name__)

# Taxa de amostragem usada só para medir energia. 8 kHz é mais que suficiente
# para um envelope de volume e mantém o array pequeno.
SAMPLE_RATE = 8000


@dataclass
class Segment:
    """Um trecho candidato a corte."""

    start: float
    end: float
    score: float
    audio_score: float = 0.0
    keyword_score: float = 0.0
    speech_score: float = 0.0
    text: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


class Analyzer:
    def __init__(self, analysis: AnalysisConfig, clip: ClipConfig):
        self.cfg = analysis
        self.clip = clip

    def find_segments(
        self,
        video_path: Path,
        cues: list[Cue],
        duration: float,
        limit: int | None = None,
    ) -> list[Segment]:
        """Devolve os melhores trechos, sem sobreposição, do melhor para o pior."""
        limit = limit or self.clip.max_clips_per_source

        envelope = self._audio_envelope(video_path)
        if envelope is None:
            log.warning("Sem áudio analisável em %s; usando só a legenda", video_path)
            envelope = np.zeros(1)

        window = self.cfg.window
        target = self.clip.target_duration

        # Faixa útil do vídeo, descontando intro e encerramento.
        first = self.cfg.skip_intro
        last = max(duration - self.cfg.skip_outro, first + target)
        if last - first < self.clip.min_duration:
            # Vídeo curto demais para descartar as pontas: usa ele inteiro.
            first, last = 0.0, duration

        # Passo de meio alvo: varre o vídeo sem gerar candidatos redundantes.
        step = max(target / 2, 1.0)
        candidates: list[Segment] = []
        cursor = first
        while cursor + self.clip.min_duration <= last:
            end = min(cursor + target, last)
            if end - cursor < self.clip.min_duration:
                break
            candidates.append(self._score(cursor, end, envelope, window, cues))
            cursor += step

        if not candidates:
            return []

        self._normalize(candidates)
        candidates.sort(key=lambda s: s.score, reverse=True)

        chosen = self._pick_non_overlapping(candidates, limit)
        return [self._snap(seg, cues, duration) for seg in chosen]

    # ------------------------------------------------------------- pontuação

    def _score(
        self,
        start: float,
        end: float,
        envelope: np.ndarray,
        window: float,
        cues: list[Cue],
    ) -> Segment:
        i0 = int(start / window)
        i1 = max(int(end / window), i0 + 1)
        chunk = envelope[i0:i1]
        audio = float(chunk.mean()) if chunk.size else 0.0

        text = text_between(cues, start, end)
        lowered = text.lower()
        hits = sum(1 for kw in self.cfg.keywords if kw in lowered)

        words = len(text.split())
        speech = words / max(end - start, 1.0)

        return Segment(
            start=start,
            end=end,
            score=0.0,  # preenchido em _normalize
            audio_score=audio,
            keyword_score=float(hits),
            speech_score=speech,
            text=text,
        )

    def _normalize(self, segments: list[Segment]) -> None:
        """Coloca os três sinais na mesma escala antes de somar com os pesos.

        Sem isso, a energia do áudio (que varia numa faixa qualquer) dominaria
        a contagem de palavras-chave (0, 1, 2...).
        """

        def scale(values: list[float]) -> list[float]:
            lo, hi = min(values), max(values)
            if hi - lo < 1e-9:
                return [0.0] * len(values)
            return [(v - lo) / (hi - lo) for v in values]

        audio = scale([s.audio_score for s in segments])
        keyword = scale([s.keyword_score for s in segments])
        speech = scale([s.speech_score for s in segments])

        for seg, a, k, sp in zip(segments, audio, keyword, speech):
            seg.score = (
                a * self.cfg.weight_audio
                + k * self.cfg.weight_keywords
                + sp * self.cfg.weight_speech
            )

    def _pick_non_overlapping(self, ranked: list[Segment], limit: int) -> list[Segment]:
        """Guloso: pega o melhor e descarta o que encostar nele."""
        chosen: list[Segment] = []
        gap = self.clip.min_gap_between_clips
        for seg in ranked:
            if len(chosen) >= limit:
                break
            if any(seg.start < c.end + gap and c.start < seg.end + gap for c in chosen):
                continue
            chosen.append(seg)
        return sorted(chosen, key=lambda s: s.start)

    def _snap(self, seg: Segment, cues: list[Cue], duration: float) -> Segment:
        """Alinha o corte com o começo de uma fala.

        Cortar no meio de uma palavra é a forma mais rápida de perder o
        espectador nos primeiros 3 segundos.
        """
        if not cues:
            return seg

        # Começa na cue que abre mais perto do início pretendido.
        starts = [c.start for c in cues if abs(c.start - seg.start) <= 2.5]
        new_start = min(starts, key=lambda s: abs(s - seg.start)) if starts else seg.start

        # Termina no fim de uma cue — a mais próxima da duração alvo, não a mais
        # distante: esticar o corte até o máximo derruba a retenção, e o Shorts
        # favorece justamente a faixa curta.
        wanted = new_start + self.clip.target_duration
        ends = [
            c.end
            for c in cues
            if new_start + self.clip.min_duration <= c.end <= new_start + self.clip.max_duration
        ]
        new_end = min(ends, key=lambda e: abs(e - wanted)) if ends else min(
            new_start + seg.duration, duration
        )
        new_end = min(new_end, duration)

        if new_end - new_start < self.clip.min_duration:
            return seg

        seg.start, seg.end = new_start, new_end
        seg.text = text_between(cues, new_start, new_end)
        return seg

    # ----------------------------------------------------------------- áudio

    def _audio_envelope(self, video_path: Path) -> np.ndarray | None:
        """Envelope RMS do áudio, uma amostra por janela de configuração."""
        cmd = [
            "ffmpeg", "-nostdin", "-v", "error",
            "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log.warning("Extração de áudio falhou: %s", exc)
            return None

        if not proc.stdout:
            return None

        samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        per_window = max(int(SAMPLE_RATE * self.cfg.window), 1)
        usable = (samples.size // per_window) * per_window
        if usable == 0:
            return np.zeros(1)

        frames = samples[:usable].reshape(-1, per_window)
        return np.sqrt((frames**2).mean(axis=1))

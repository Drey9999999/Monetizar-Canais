"""Legendas: leitura de VTT do YouTube e geração de ASS animado.

As legendas automáticas do YouTube trazem o tempo de cada palavra embutido no
VTT (`<00:00:01.240><c>palavra</c>`). Isso é o que permite gerar legenda
karaokê palavra a palavra sem precisar rodar transcrição própria.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

# "00:00:01.240 --> 00:00:03.500 align:start position:0%"
CUE_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2}[.,]\d{3})"
)
# Timestamp inline de palavra: <00:00:01.240>
INLINE_TS_RE = re.compile(r"<(\d{2}:\d{2}:\d{2}[.,]\d{3})>")
TAG_RE = re.compile(r"</?[cbiu][^>]*>")


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Cue:
    """Uma linha de legenda, com as palavras individuais quando disponíveis."""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    def shifted(self, offset: float) -> "Cue":
        """Move a cue no tempo — usado ao recortar um trecho do vídeo."""
        return Cue(
            start=self.start - offset,
            end=self.end - offset,
            text=self.text,
            words=[Word(w.text, w.start - offset, w.end - offset) for w in self.words],
        )


def parse_vtt(path: Path) -> list[Cue]:
    """Lê um WebVTT e devolve as cues com timing de palavra quando houver."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    cues: list[Cue] = []

    blocks = re.split(r"\n\s*\n", raw)
    for block in blocks:
        match = CUE_RE.search(block)
        if not match:
            continue
        start = _to_seconds(match.group("start"))
        end = _to_seconds(match.group("end"))
        # O corpo é tudo depois da linha do timestamp.
        body_lines = block[match.end():].split("\n")[1:]
        body = "\n".join(body_lines).strip()
        if not body:
            continue

        words = _parse_words(body, start, end)
        text = _strip(body)
        if not text:
            continue
        cues.append(Cue(start=start, end=end, text=text, words=words))

    return _dedupe(cues)


def _parse_words(body: str, cue_start: float, cue_end: float) -> list[Word]:
    """Extrai palavras com timing individual do corpo de uma cue."""
    if not INLINE_TS_RE.search(body):
        return []

    words: list[Word] = []
    # Divide mantendo os timestamps como separadores, para casar cada
    # timestamp com o texto que vem logo depois dele.
    parts = INLINE_TS_RE.split(body)
    # parts = [texto_antes, ts1, texto1, ts2, texto2, ...]
    leading = _strip(parts[0])
    if leading:
        words.append(Word(leading, cue_start, cue_start))

    for i in range(1, len(parts) - 1, 2):
        ts = _to_seconds(parts[i])
        chunk = _strip(parts[i + 1])
        if chunk:
            words.append(Word(chunk, ts, ts))

    # Cada palavra termina onde a próxima começa; a última vai até o fim da cue.
    for i, word in enumerate(words):
        word.end = words[i + 1].start if i + 1 < len(words) else cue_end
        if word.end <= word.start:
            word.end = word.start + 0.15
    return words


def _dedupe(cues: list[Cue]) -> list[Cue]:
    """Remove a repetição de linhas típica da legenda automática do YouTube.

    A legenda automática rola linha a linha: cada cue repete a anterior mais uma
    palavra nova. Sem limpar isso, a legenda queimada aparece duplicada.
    """
    out: list[Cue] = []
    for cue in cues:
        if out and (cue.text == out[-1].text or cue.text.startswith(out[-1].text)):
            # Mantém a versão mais completa e estende o tempo.
            if len(cue.text) >= len(out[-1].text):
                merged_words = cue.words or out[-1].words
                out[-1] = Cue(out[-1].start, cue.end, cue.text, merged_words)
            continue
        out.append(cue)
    return out


def slice_cues(cues: list[Cue], start: float, end: float) -> list[Cue]:
    """Recorta as cues de um intervalo e rebase o tempo para começar em zero."""
    out: list[Cue] = []
    for cue in cues:
        if cue.end <= start or cue.start >= end:
            continue
        clipped = Cue(
            start=max(cue.start, start),
            end=min(cue.end, end),
            text=cue.text,
            words=[w for w in cue.words if w.end > start and w.start < end],
        )
        out.append(clipped.shifted(start))
    return out


def text_between(cues: list[Cue], start: float, end: float) -> str:
    """Junta o texto falado num intervalo — base para título e descrição."""
    return " ".join(c.text for c in cues if c.end > start and c.start < end).strip()


# --------------------------------------------------------------------- ASS

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,{margin_h},{margin_h},{margin_v},1
Style: Hook,{font},{hook_size},&H0000F0FF,&H0000F0FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},3,8,{margin_h},{margin_h},{hook_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Amarelo destacando a palavra falada. &HBBGGRR no formato ASS.
HIGHLIGHT = r"{\c&H00F0FF&}"
NORMAL = r"{\c&HFFFFFF&}"


def build_ass(
    cues: list[Cue],
    *,
    width: int,
    height: int,
    style: str = "karaoke",
    hook: str | None = None,
    hook_duration: float = 3.0,
    font: str = "DejaVu Sans",
) -> str:
    """Gera um arquivo ASS pronto para o filtro `subtitles` do ffmpeg."""
    size = max(int(height * 0.045), 24)
    header = ASS_HEADER.format(
        width=width,
        height=height,
        font=font,
        size=size,
        hook_size=int(size * 1.25),
        outline=max(int(size * 0.09), 3),
        margin_h=int(width * 0.08),
        margin_v=int(height * 0.18),
        hook_margin=int(height * 0.55),
    )

    lines: list[str] = []
    if hook:
        # O gancho é maiúsculo e 25% maior que a legenda, e maiúscula ocupa mais
        # largura por caractere — daí uma quebra bem mais curta que a da legenda.
        lines.append(
            _event("Hook", 0.0, hook_duration, _wrap(_escape(hook.upper()), 14))
        )

    for cue in cues:
        if cue.end <= 0:
            continue
        start = max(cue.start, 0.0)
        if style == "karaoke" and cue.words:
            lines.extend(_karaoke_events(cue, start))
        else:
            lines.append(_event("Caption", start, cue.end, _wrap(_escape(cue.text), 26)))

    return header + "\n".join(lines) + "\n"


def _karaoke_events(cue: Cue, start: float) -> list[str]:
    """Uma cue vira N eventos: a cada palavra, o texto todo é redesenhado com
    aquela palavra destacada. É o efeito palavra-a-palavra que segura retenção.
    """
    events: list[str] = []
    words = cue.words
    for i, word in enumerate(words):
        seg_start = max(word.start, start)
        seg_end = max(word.end, seg_start + 0.05)
        pieces = []
        for j, other in enumerate(words):
            colour = HIGHLIGHT if j == i else NORMAL
            pieces.append(f"{colour}{_escape(other.text)}")
        events.append(_event("Caption", seg_start, seg_end, _wrap(" ".join(pieces), 26)))
    return events


def _event(style: str, start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}"


def _wrap(text: str, width: int) -> str:
    """Quebra em linhas curtas com \\N — linha longa some na tela do celular."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        # Ignora as tags de cor ao medir o comprimento visível.
        visible = len(re.sub(r"\{[^}]*\}", "", current + " " + word))
        if current and visible > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return r"\N".join(lines)


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _strip(text: str) -> str:
    """Tira tags VTT, entidades HTML e espaços redundantes."""
    text = INLINE_TS_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _to_seconds(stamp: str) -> float:
    hours, minutes, rest = stamp.split(":")
    seconds = float(rest.replace(",", "."))
    return int(hours) * 3600 + int(minutes) * 60 + seconds


def _ass_time(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):d}:{int(minutes):02d}:{secs:05.2f}"

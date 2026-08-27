"""Geração de gancho, título, descrição e hashtags para cada corte.

Tudo aqui é heurístico e determinístico — sem chamada de modelo. A intenção é
produzir um rascunho bom o bastante para revisar em segundos, não substituir a
escrita humana. O gancho é o campo que mais importa: 50-60% de quem desiste do
vídeo desiste nos primeiros 3 segundos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .analyzer import Segment
from .downloader import VideoInfo

# Limites das plataformas.
YT_TITLE_MAX = 100
TIKTOK_CAPTION_MAX = 2200

# Aberturas que funcionam como gancho quando a fala já começa forte.
HOOK_TEMPLATES = [
    "VOCÊ PRECISA VER ISSO",
    "NINGUÉM TE CONTA ISSO",
    "ISSO MUDA TUDO",
    "PRESTA ATENÇÃO NISSO",
    "O QUE NINGUÉM FALA",
]

# Palavras vazias que não servem como assunto do título.
STOPWORDS = {
    "a", "o", "e", "de", "da", "do", "que", "em", "um", "uma", "para", "com",
    "não", "os", "as", "no", "na", "se", "por", "mais", "como", "mas", "ao",
    "isso", "isso,", "ele", "ela", "eu", "você", "voce", "the", "and", "of",
    "to", "in", "is", "it", "that", "this", "you", "for", "on", "with", "é",
    "são", "foi", "vai", "tem", "ser", "está", "meu", "sua", "seu", "então",
    "quando", "porque", "muito", "já", "aí", "lá", "tá", "né", "assim",
}


@dataclass
class ClipMetadata:
    hook: str
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)

    @property
    def caption(self) -> str:
        """Legenda pronta para colar no TikTok/Shorts, com as hashtags no fim."""
        tags = " ".join(self.hashtags)
        return f"{self.description}\n\n{tags}".strip()[:TIKTOK_CAPTION_MAX]


def build_metadata(
    segment: Segment,
    source: VideoInfo,
    *,
    channel_name: str = "",
    base_hashtags: list[str] | None = None,
    credit: bool = True,
) -> ClipMetadata:
    """Monta os metadados de um corte a partir da fala e do vídeo de origem."""
    spoken = _clean(segment.text)
    hook = _make_hook(spoken)
    title = _make_title(spoken, source, hook)
    description = _make_description(spoken, source, channel_name, credit)
    hashtags = _make_hashtags(spoken, source, base_hashtags or [])
    return ClipMetadata(hook=hook, title=title, description=description, hashtags=hashtags)


def _make_hook(spoken: str) -> str:
    """Escolhe o texto sobreposto nos primeiros segundos.

    A primeira frase falada costuma ser o melhor gancho possível, porque o texto
    na tela passa a confirmar o áudio em vez de competir com ele. Só cai no
    template genérico quando não há frase curta o suficiente para caber.
    """
    sentences = _sentences(spoken)
    for sentence in sentences[:2]:
        words = sentence.split()
        if 3 <= len(words) <= 7:
            return sentence.rstrip(".").upper()

    # Legenda automática do YouTube vem sem pontuação, então o texto todo costuma
    # virar uma "frase" só e cair aqui. Corta curto: gancho longo não cabe em
    # duas linhas na tela do celular e some antes de ser lido.
    if sentences:
        words = sentences[0].split()[:4]
        if len(words) >= 3:
            return " ".join(words).rstrip(".,").upper()

    # Sem legenda utilizável: alterna entre os templates de forma estável, para
    # que dois cortes seguidos do mesmo vídeo não recebam o mesmo gancho.
    return HOOK_TEMPLATES[hash(spoken) % len(HOOK_TEMPLATES)]


def _make_title(spoken: str, source: VideoInfo, hook: str) -> str:
    """Título curto. No Shorts o título é lido, mas o gancho visual pesa mais."""
    sentences = _sentences(spoken)
    candidate = sentences[0] if sentences else source.title
    candidate = candidate.strip().rstrip(".")

    if len(candidate) < 15:
        candidate = hook.capitalize()

    if len(candidate) > YT_TITLE_MAX - 10:
        candidate = candidate[: YT_TITLE_MAX - 13].rsplit(" ", 1)[0] + "..."

    return f"{candidate} #shorts"[:YT_TITLE_MAX]


def _make_description(
    spoken: str, source: VideoInfo, channel_name: str, credit: bool
) -> str:
    """Descrição com crédito à fonte.

    Creditar não torna o corte legal por si só — não substitui licença nem fair
    use —, mas é o mínimo esperado e ajuda na avaliação de conteúdo reutilizado.
    """
    parts: list[str] = []
    snippet = spoken[:180].rsplit(" ", 1)[0] if len(spoken) > 180 else spoken
    if snippet:
        parts.append(f'"{snippet}..."' if len(spoken) > 180 else f'"{snippet}"')

    if credit and source.channel:
        parts.append(f"Trecho do canal {source.channel}.")
        parts.append(f"Vídeo completo: {source.url}")

    if channel_name:
        parts.append(f"Mais cortes assim em {channel_name}. Inscreva-se.")

    return "\n\n".join(p for p in parts if p)


def _make_hashtags(spoken: str, source: VideoInfo, base: list[str]) -> list[str]:
    """Junta hashtags fixas do canal com termos frequentes da própria fala."""
    tags = [_as_hashtag(t) for t in base]
    for default in ("#shorts", "#fyp", "#viral"):
        if default not in tags:
            tags.append(default)

    for keyword in _keywords(spoken, limit=3):
        tag = _as_hashtag(keyword)
        if tag not in tags:
            tags.append(tag)

    for tag in (source.tags or [])[:2]:
        candidate = _as_hashtag(tag)
        if len(candidate) > 3 and candidate not in tags:
            tags.append(candidate)

    return tags[:12]


def _keywords(text: str, limit: int = 3) -> list[str]:
    """Palavras mais repetidas da fala, ignorando as vazias."""
    counts: dict[str, int] = {}
    for word in re.findall(r"\b[\wÀ-ÿ]{4,}\b", text.lower()):
        if word in STOPWORDS:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:limit]]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 2]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _as_hashtag(word: str) -> str:
    slug = re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", word)
    return f"#{slug.lower()}" if slug else ""

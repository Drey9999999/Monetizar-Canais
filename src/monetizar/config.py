"""Carregamento e validação da configuração do projeto."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "config.example.yaml"


@dataclass
class DownloadConfig:
    """Como o yt-dlp baixa o material bruto."""

    # Limita a resolução da fonte: 1080p já é mais do que o Shorts/TikTok usam
    # (1080x1920) e evita baixar arquivos de 4K por nada.
    max_height: int = 1080
    # Formato de saída do download. mp4 evita remux caro na hora de cortar.
    container: str = "mp4"
    # Idiomas de legenda automática a tentar, em ordem de preferência.
    subtitle_languages: list[str] = field(default_factory=lambda: ["pt", "pt-BR", "en"])
    # Não baixa vídeos mais longos que isso (segundos). Lives de 6h são armadilha.
    max_duration: int = 7200
    # Nem mais curtos que isso: vídeo curto demais não tem corte bom dentro.
    min_duration: int = 120
    # Arquivo de cookies do navegador, para vídeos com restrição de idade.
    cookies_file: str | None = None
    # Espera entre downloads (segundos), para não martelar a origem.
    sleep_interval: float = 2.0
    # Limite de taxa, ex.: "5M". None = sem limite.
    rate_limit: str | None = None


@dataclass
class ClipConfig:
    """Parâmetros do corte e da edição."""

    # Duração alvo do corte. O algoritmo do Shorts favorece 20-35s; o Creator
    # Rewards do TikTok só paga em vídeos com 60s+ — daí os dois perfis.
    target_duration: float = 30.0
    min_duration: float = 15.0
    max_duration: float = 59.0
    # Quantos cortes extrair, no máximo, de um mesmo vídeo fonte.
    max_clips_per_source: int = 3
    # Distância mínima entre dois cortes do mesmo vídeo (segundos), para não
    # gerar dois clipes praticamente iguais.
    min_gap_between_clips: float = 45.0
    # Resolução de saída (vertical).
    width: int = 1080
    height: int = 1920
    fps: int = 30
    # Estratégia de reenquadramento: "blur" (fundo desfocado), "crop" (corte
    # central) ou "pad" (barras pretas).
    reframe: str = "blur"
    # Queimar legendas no vídeo. 85% dos Shorts começam sem som — sem legenda
    # o gancho não é lido.
    burn_captions: bool = True
    # Estilo de legenda: "karaoke" (palavra a palavra) ou "block".
    caption_style: str = "karaoke"
    # Texto de gancho sobreposto nos primeiros segundos.
    hook_enabled: bool = True
    hook_duration: float = 3.0
    # Normalização de loudness para -14 LUFS (padrão das plataformas).
    normalize_audio: bool = True
    # Bitrate de vídeo alvo.
    video_bitrate: str = "8M"
    audio_bitrate: str = "192k"
    # CRF usado quando bitrate alvo não se aplica.
    crf: int = 20
    preset: str = "medium"


@dataclass
class AnalysisConfig:
    """Como os melhores momentos são escolhidos."""

    # Janela de amostragem do envelope de áudio (segundos).
    window: float = 1.0
    # Peso da energia do áudio na nota final do segmento.
    weight_audio: float = 1.0
    # Peso das palavras-chave de gancho encontradas na legenda.
    weight_keywords: float = 1.5
    # Peso da densidade de fala (palavras por segundo).
    weight_speech: float = 0.8
    # Ignora os primeiros/últimos N segundos do vídeo (intro e call-to-action
    # do canal original raramente rendem corte).
    skip_intro: float = 20.0
    skip_outro: float = 20.0
    # Palavras que sinalizam um momento com potencial de gancho.
    keywords: list[str] = field(
        default_factory=lambda: [
            "segredo", "ninguém", "nunca", "erro", "verdade", "descobri",
            "impossível", "chocante", "olha isso", "acredita", "por isso",
            "o problema", "a razão", "na real", "sinceramente", "atenção",
            "secret", "nobody", "never", "mistake", "truth", "actually",
            "the reason", "here's why", "listen", "crazy", "insane",
        ]
    )


@dataclass
class PublishConfig:
    """Ritmo de publicação."""

    # Vídeos por dia por canal.
    videos_per_day: int = 5
    # Horários alvo (hora local, 24h). Espalhados ao longo do dia ativo.
    slots: list[str] = field(
        default_factory=lambda: ["08:00", "12:00", "15:00", "18:00", "21:00"]
    )
    # Quantos dias de conteúdo manter prontos na fila.
    buffer_days: int = 3
    timezone: str = "America/Sao_Paulo"


@dataclass
class PathsConfig:
    """Onde cada coisa fica em disco."""

    data: str = "data"
    downloads: str = "data/downloads"
    clips: str = "data/clips"
    work: str = "data/work"
    database: str = "data/monetizar.db"

    def resolve(self, root: Path) -> None:
        """Converte caminhos relativos em absolutos, ancorados no repositório."""
        for f in fields(self):
            value = getattr(self, f.name)
            path = Path(value)
            if not path.is_absolute():
                path = root / path
            setattr(self, f.name, str(path))

    def ensure_dirs(self) -> None:
        for name in ("data", "downloads", "clips", "work"):
            Path(getattr(self, name)).mkdir(parents=True, exist_ok=True)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    download: DownloadConfig = field(default_factory=DownloadConfig)
    clip: ClipConfig = field(default_factory=ClipConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    publish: PublishConfig = field(default_factory=PublishConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    root: Path = field(default=REPO_ROOT)

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Config":
        """Carrega o YAML de configuração, caindo no exemplo e nos defaults."""
        candidates = [Path(path)] if path else [DEFAULT_CONFIG_PATH, EXAMPLE_CONFIG_PATH]
        raw: dict[str, Any] = {}
        for candidate in candidates:
            if candidate.exists():
                raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                break
        else:
            if path:
                raise FileNotFoundError(f"Config não encontrada: {path}")

        cfg = cls(
            download=_build(DownloadConfig, raw.get("download")),
            clip=_build(ClipConfig, raw.get("clip")),
            analysis=_build(AnalysisConfig, raw.get("analysis")),
            publish=_build(PublishConfig, raw.get("publish")),
            paths=_build(PathsConfig, raw.get("paths")),
        )
        cfg.paths.resolve(cfg.root)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        clip = self.clip
        if not clip.min_duration <= clip.target_duration <= clip.max_duration:
            raise ValueError(
                "clip.target_duration precisa ficar entre min_duration e max_duration "
                f"({clip.min_duration}..{clip.max_duration}, recebido {clip.target_duration})"
            )
        if clip.reframe not in {"blur", "crop", "pad"}:
            raise ValueError(f"clip.reframe inválido: {clip.reframe}")
        if clip.caption_style not in {"karaoke", "block"}:
            raise ValueError(f"clip.caption_style inválido: {clip.caption_style}")
        if self.publish.videos_per_day < 1:
            raise ValueError("publish.videos_per_day precisa ser >= 1")
        if len(self.publish.slots) < self.publish.videos_per_day:
            raise ValueError(
                f"publish.slots tem {len(self.publish.slots)} horários mas "
                f"videos_per_day é {self.publish.videos_per_day}"
            )


def _build(cls: type, raw: dict[str, Any] | None):
    """Instancia uma dataclass ignorando chaves desconhecidas do YAML."""
    if not raw:
        return cls()
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(
            f"Chaves desconhecidas em {cls.__name__.replace('Config', '').lower()}: "
            f"{', '.join(sorted(unknown))}"
        )
    return cls(**{k: v for k, v in raw.items() if k in known})

"""Testes do downloader: erros do YouTube, formato, legenda e arquivo final.

Todos os casos aqui vieram de rodar o pipeline contra o YouTube de verdade.
Nenhum deles toca a rede: o que se testa é como o projeto reage ao que o
yt-dlp devolve, não o yt-dlp.
"""

from __future__ import annotations

import pytest
from yt_dlp.utils import DownloadError

from monetizar.config import Config
from monetizar.downloader import (
    Downloader,
    ExtractionError,
    SourceUnavailable,
    _find_media,
    _lang_tag,
    _parse_rate,
    classify_error,
)


@pytest.fixture
def downloader(tmp_path):
    cfg = Config()
    cfg.paths.downloads = str(tmp_path / "downloads")
    cfg.download.extract_retries = 3
    cfg.download.retry_backoff = 0.0
    cfg.download.retry_backoff_max = 0.0
    return Downloader(cfg)


# ------------------------------------------------------ classificação de erro


@pytest.mark.parametrize(
    "mensagem",
    [
        'query "x" page 1: Unable to download API page: HTTP Error 403: Forbidden',
        "HTTP Error 429: Too Many Requests",
        "Sign in to confirm you’re not a bot. Use --cookies-from-browser",
        "Failed to extract any player response",
        "HTTP Error 503: Service Unavailable",
    ],
)
def test_erro_transitorio_pede_nova_tentativa(mensagem):
    """403/429/checagem de bot são do momento — vale repetir."""
    assert classify_error(DownloadError(mensagem)) is None


@pytest.mark.parametrize(
    "mensagem,motivo",
    [
        ("Sign in to confirm your age. This video may be inappropriate", "idade"),
        ("Private video. Sign in if you've been granted access", "privado"),
        ("Join this channel to get access to members-only content", "membros"),
        ("The uploader has not made this video available in your country", "regiao"),
        ("Video unavailable. This video has been removed by the uploader", "removido"),
    ],
)
def test_erro_do_video_nao_vale_nova_tentativa(mensagem, motivo):
    assert classify_error(DownloadError(mensagem)) == motivo


def test_restricao_de_idade_ganha_do_403():
    """Vídeo com restrição de idade também responde 403.

    Se o 403 fosse checado primeiro, o projeto gastaria todas as tentativas
    num vídeo que nunca vai baixar sem cookies.
    """
    exc = DownloadError(
        "HTTP Error 403: Forbidden. Sign in to confirm your age. "
        "This video may be inappropriate for some users."
    )

    assert classify_error(exc) == "idade"


# -------------------------------------------------------------------- retries


def test_extract_repete_ate_passar(downloader, monkeypatch):
    """Um 403 no meio do caminho não pode virar 'nenhum resultado'."""
    tentativas = []

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, target, download=False):
            tentativas.append(target)
            if len(tentativas) < 3:
                raise DownloadError("HTTP Error 403: Forbidden")
            return {"id": "abc", "title": "ok"}

    monkeypatch.setattr("monetizar.downloader.YoutubeDL", FakeYDL)

    assert downloader._extract({}, "ytsearch1:x") == {"id": "abc", "title": "ok"}
    assert len(tentativas) == 3


def test_extract_estoura_em_vez_de_devolver_vazio(downloader, monkeypatch):
    """O bug original: busca bloqueada saía como lista vazia na tela."""

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, target, download=False):
            raise DownloadError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr("monetizar.downloader.YoutubeDL", FakeYDL)

    with pytest.raises(ExtractionError, match="3 tentativas"):
        downloader.search("podcast brasileiro")


def test_video_indisponivel_nao_gasta_tentativas(downloader, monkeypatch):
    chamadas = []

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, target, download=False):
            chamadas.append(target)
            raise DownloadError("Sign in to confirm your age")

    monkeypatch.setattr("monetizar.downloader.YoutubeDL", FakeYDL)

    with pytest.raises(SourceUnavailable) as info:
        downloader._extract({}, "https://youtu.be/x")

    assert info.value.reason == "idade"
    assert len(chamadas) == 1


# ------------------------------------------------------------ arquivo baixado


def test_find_media_ignora_restos_de_formato(tmp_path):
    """Regressão: "source.f137.mp4" ordena antes de "source.mp4".

    Pegar o primeiro do glob devolvia o stream só-vídeo do download adaptativo
    e o corte saía mudo.
    """
    (tmp_path / "source.f137.mp4").write_bytes(b"video sem audio")
    (tmp_path / "source.f140.m4a").write_bytes(b"audio")
    (tmp_path / "source.mp4").write_bytes(b"merge pronto")

    assert _find_media(tmp_path) == tmp_path / "source.mp4"


def test_find_media_nao_devolve_fragmento_quando_o_merge_falhou(tmp_path):
    """Sem o arquivo final não há vídeo utilizável — melhor dizer isso."""
    (tmp_path / "source.f137.mp4").write_bytes(b"video sem audio")

    assert _find_media(tmp_path) is None


def test_find_media_aceita_outro_container(tmp_path):
    (tmp_path / "source.webm").write_bytes(b"x")

    assert _find_media(tmp_path) == tmp_path / "source.webm"


# ------------------------------------------------------------------- legendas


@pytest.mark.parametrize(
    "nome,tag",
    [
        ("source.pt.vtt", "pt"),
        ("source.pt-BR.vtt", "pt-br"),
        ("source.pt-orig.vtt", "pt-orig"),
        ("source.vtt", ""),
    ],
)
def test_lang_tag(tmp_path, nome, tag):
    assert _lang_tag(tmp_path / nome) == tag


def test_legenda_pt_cobre_pt_br(downloader):
    pasta = downloader.dest / "vid"
    pasta.mkdir(parents=True)
    (pasta / "source.pt-BR.vtt").write_text("x")

    assert downloader._pick_subtitle(pasta) == pasta / "source.pt-BR.vtt"


def test_legenda_prefere_a_tag_exata(downloader):
    pasta = downloader.dest / "vid"
    pasta.mkdir(parents=True)
    (pasta / "source.pt-BR.vtt").write_text("x")
    (pasta / "source.pt.vtt").write_text("x")

    assert downloader._pick_subtitle(pasta) == pasta / "source.pt.vtt"


def test_legenda_respeita_a_ordem_configurada(downloader):
    downloader.dl_cfg.subtitle_languages = ["pt", "en"]
    pasta = downloader.dest / "vid"
    pasta.mkdir(parents=True)
    (pasta / "source.en.vtt").write_text("x")
    (pasta / "source.pt.vtt").write_text("x")

    assert downloader._pick_subtitle(pasta) == pasta / "source.pt.vtt"


def test_legenda_em_idioma_nao_configurado_e_descartada(downloader):
    """Legenda em espanhol queimada num canal pt é pior que corte sem legenda."""
    downloader.dl_cfg.subtitle_languages = ["pt", "en"]
    pasta = downloader.dest / "vid"
    pasta.mkdir(parents=True)
    (pasta / "source.es.vtt").write_text("x")

    assert downloader._pick_subtitle(pasta) is None


# -------------------------------------------------------------------- formato


def test_seletor_de_formato_tem_degrau_adaptativo(downloader):
    """Sem o degrau do meio a queda vai direto para `best` — 360p no YouTube."""
    downloader.dl_cfg.max_height = 1080

    degraus = downloader._format_selector().split("/")

    assert degraus[0] == "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
    assert degraus[1] == "bestvideo[height<=1080]+bestaudio"
    assert "best[height<=1080]" in degraus


# ---------------------------------------------------------------- rate limit


def test_rate_limit_malformado_nao_derruba_o_run():
    assert _parse_rate("5 Mbps") is None
    assert _parse_rate("abc") is None

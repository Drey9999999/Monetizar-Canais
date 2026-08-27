"""Como o pipeline reage quando o YouTube recusa uma busca ou um vídeo.

Casos observados rodando contra o YouTube de verdade: busca bloqueada por 403,
vídeo pedindo confirmação de bot, fonte com restrição de idade.
"""

from __future__ import annotations

import pytest

from monetizar.channels import Channel
from monetizar.config import Config
from monetizar.downloader import ExtractionError, SourceUnavailable, VideoInfo
from monetizar.pipeline import Pipeline, RunReport


@pytest.fixture
def config(tmp_path):
    cfg = Config()
    cfg.paths.data = str(tmp_path)
    cfg.paths.downloads = str(tmp_path / "downloads")
    cfg.paths.clips = str(tmp_path / "clips")
    cfg.paths.work = str(tmp_path / "work")
    cfg.paths.database = str(tmp_path / "monetizar.db")
    return cfg


@pytest.fixture
def pipeline(config):
    return Pipeline(config)


def _video(vid: str = "abc123") -> VideoInfo:
    return VideoInfo(
        video_id=vid,
        title=f"Vídeo {vid}",
        url=f"https://www.youtube.com/watch?v={vid}",
        duration=900.0,
        view_count=1000,
        like_count=50,
    )


def _canal(**kwargs) -> Channel:
    base = dict(slug="c1", name="Canal", queries=["algo"], platforms=["youtube"])
    return Channel(**{**base, **kwargs})


class FakeDownloader:
    """Downloader de mentira: cada método devolve ou levanta o que o teste pedir."""

    def __init__(self, *, search=None, download=None):
        self._search = search or []
        self._download = download

    def search(self, query, limit=10):
        if isinstance(self._search, Exception):
            raise self._search
        return list(self._search)

    def channel_videos(self, url, limit=20):
        return []

    def filter_candidates(self, videos):
        return list(videos)

    def download(self, video):
        if isinstance(self._download, Exception):
            raise self._download
        return self._download


# ------------------------------------------------------------------ descoberta


def test_busca_bloqueada_vira_erro_no_relatorio(pipeline):
    """O bug original: 403 do YouTube saía como 'nenhum candidato'."""
    pipeline.downloader = FakeDownloader(
        search=ExtractionError("YouTube recusou 4 tentativas")
    )
    report = RunReport(channel="c1")

    candidatos = pipeline.discover(_canal(), report=report)

    assert candidatos == []
    assert len(report.errors) == 1
    assert "recusou" in report.errors[0]


def test_uma_busca_ruim_nao_derruba_as_outras(pipeline):
    chamadas = []

    class Parcial(FakeDownloader):
        def search(self, query, limit=10):
            chamadas.append(query)
            if query == "ruim":
                raise ExtractionError("429")
            return [_video("ok1")]

    pipeline.downloader = Parcial()
    report = RunReport(channel="c1")

    candidatos = pipeline.discover(
        _canal(queries=["ruim", "boa"]), report=report
    )

    assert chamadas == ["ruim", "boa"]
    assert [v.video_id for v in candidatos] == ["ok1"]
    assert len(report.errors) == 1


def test_busca_sem_resultado_nao_gera_erro(pipeline):
    pipeline.downloader = FakeDownloader(search=[])
    report = RunReport(channel="c1")

    assert pipeline.discover(_canal(), report=report) == []
    assert report.errors == []


# --------------------------------------------------------------------- fontes


def test_fonte_com_restricao_de_idade_e_pulada_com_motivo(pipeline):
    pipeline.downloader = FakeDownloader(
        search=[_video()], download=SourceUnavailable("idade", "Sign in to confirm")
    )

    report = pipeline.run(_canal(), max_sources=1)

    assert report.clip_count == 0
    assert report.skipped == 1
    assert "idade" in report.errors[0]


def test_fonte_indisponivel_fica_registrada_e_nao_volta(pipeline):
    """Regressão: `mark_source` é UPDATE e a fonte nunca tinha sido inserida.

    O UPDATE não gravava nada, então o mesmo vídeo com restrição de idade
    reaparecia na descoberta a cada run para falhar de novo.
    """
    pipeline.downloader = FakeDownloader(
        search=[_video()], download=SourceUnavailable("idade")
    )

    pipeline.run(_canal(), max_sources=1)

    assert pipeline.db.has_source("abc123")


def test_fonte_com_erro_inesperado_tambem_fica_registrada(pipeline):
    pipeline.downloader = FakeDownloader(
        search=[_video()], download=RuntimeError("disco cheio")
    )

    report = pipeline.run(_canal(), max_sources=1)

    assert pipeline.db.has_source("abc123")
    assert "disco cheio" in report.errors[0]


def test_fonte_registrada_nao_e_redescoberta(pipeline):
    pipeline.downloader = FakeDownloader(
        search=[_video()], download=SourceUnavailable("privado")
    )
    pipeline.run(_canal(), max_sources=1)

    segundo = pipeline.run(_canal(), max_sources=1)

    assert segundo.discovered == 0

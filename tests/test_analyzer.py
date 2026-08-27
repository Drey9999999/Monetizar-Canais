"""Testes da escolha de melhores momentos."""

import numpy as np
import pytest

from monetizar.analyzer import Analyzer, Segment
from monetizar.captions import Cue
from monetizar.config import AnalysisConfig, ClipConfig


@pytest.fixture
def analyzer():
    return Analyzer(AnalysisConfig(skip_intro=0.0, skip_outro=0.0), ClipConfig())


def test_normalize_poe_os_sinais_na_mesma_escala(analyzer):
    """Sem normalizar, a energia do áudio dominaria a contagem de palavras."""
    segmentos = [
        Segment(0, 30, 0, audio_score=0.01, keyword_score=0, speech_score=1.0),
        Segment(30, 60, 0, audio_score=0.02, keyword_score=4, speech_score=2.0),
    ]

    analyzer._normalize(segmentos)

    # O segundo tem o máximo dos três sinais, então soma os três pesos.
    assert segmentos[0].score == pytest.approx(0.0)
    assert segmentos[1].score == pytest.approx(1.0 + 1.5 + 0.8)


def test_normalize_com_sinais_iguais_nao_divide_por_zero(analyzer):
    segmentos = [
        Segment(0, 30, 0, audio_score=0.5, keyword_score=1, speech_score=1.0),
        Segment(30, 60, 0, audio_score=0.5, keyword_score=1, speech_score=1.0),
    ]

    analyzer._normalize(segmentos)

    assert all(s.score == 0.0 for s in segmentos)


def test_pick_respeita_a_distancia_minima(analyzer):
    """Dois cortes colados geram clipes praticamente iguais."""
    analyzer.clip.min_gap_between_clips = 45.0
    ranked = [
        Segment(100, 130, score=10.0),
        Segment(140, 170, score=9.0),   # só 10s depois: rejeitado
        Segment(200, 230, score=8.0),   # 70s depois: aceito
    ]

    escolhidos = analyzer._pick_non_overlapping(ranked, limit=3)

    assert [s.start for s in escolhidos] == [100, 200]


def test_pick_respeita_o_limite(analyzer):
    ranked = [Segment(i * 200, i * 200 + 30, score=10 - i) for i in range(5)]

    assert len(analyzer._pick_non_overlapping(ranked, limit=2)) == 2


def test_snap_alinha_o_inicio_com_o_comeco_de_uma_fala(analyzer):
    """Cortar no meio da palavra derruba a retenção nos primeiros segundos."""
    cues = [Cue(9.2, 20.0, "comeco da fala"), Cue(20.0, 39.5, "resto da fala")]
    seg = Segment(start=10.0, end=40.0, score=1.0)

    ajustado = analyzer._snap(seg, cues, duration=120.0)

    assert ajustado.start == pytest.approx(9.2)


def test_snap_busca_o_fim_mais_proximo_da_duracao_alvo(analyzer):
    """Esticar até o máximo derruba retenção; o alvo é 30s."""
    analyzer.clip.target_duration = 30.0
    analyzer.clip.max_duration = 59.0
    cues = [
        Cue(0.0, 10.0, "a"),
        Cue(10.0, 31.0, "b"),   # 31s: perto do alvo
        Cue(31.0, 58.0, "c"),   # 58s: dentro do máximo, mas longe do alvo
    ]
    seg = Segment(start=0.0, end=30.0, score=1.0)

    ajustado = analyzer._snap(seg, cues, duration=120.0)

    assert ajustado.end == pytest.approx(31.0)


def test_snap_sem_legenda_devolve_o_segmento_intacto(analyzer):
    seg = Segment(start=10.0, end=40.0, score=1.0)

    assert analyzer._snap(seg, [], duration=120.0) is seg


def test_snap_nao_encurta_abaixo_do_minimo(analyzer):
    """Se o alinhamento produzir um corte curto demais, mantém o original."""
    analyzer.clip.min_duration = 15.0
    cues = [Cue(10.0, 12.0, "fala curta")]
    seg = Segment(start=10.0, end=40.0, score=1.0)

    ajustado = analyzer._snap(seg, cues, duration=120.0)

    assert ajustado.duration >= analyzer.clip.min_duration


def test_score_conta_palavras_chave_da_legenda(analyzer):
    analyzer.cfg.keywords = ["segredo", "ninguém"]
    cues = [Cue(0.0, 30.0, "o segredo que ninguém te conta")]

    seg = analyzer._score(0.0, 30.0, np.array([0.5] * 60), 1.0, cues)

    assert seg.keyword_score == 2.0
    assert seg.speech_score == pytest.approx(6 / 30)


def test_segment_duration():
    assert Segment(10.0, 42.5, score=1.0).duration == pytest.approx(32.5)

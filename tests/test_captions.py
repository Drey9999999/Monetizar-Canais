"""Testes do parser de VTT e do gerador de ASS."""

from pathlib import Path

import pytest

from monetizar.captions import (
    Cue,
    Word,
    build_ass,
    parse_vtt,
    slice_cues,
    text_between,
)

# Formato real da legenda automática do YouTube: a primeira palavra vem solta e
# as seguintes trazem o timestamp inline.
VTT_AUTO = """WEBVTT

00:00:05.000 --> 00:00:07.100 align:start position:0%
olha<00:00:05.400><c> isso</c><00:00:05.900><c> aqui</c>

00:00:07.100 --> 00:00:09.500 align:start position:0%
o<00:00:07.500><c> segredo</c><00:00:08.000><c> completo</c>
"""

VTT_SIMPLES = """WEBVTT

00:00:01.000 --> 00:00:03.000
primeira linha

00:00:03.000 --> 00:00:05.000
segunda linha
"""

# A legenda automática repete a linha anterior acrescida de uma palavra.
VTT_ROLANTE = """WEBVTT

00:00:01.000 --> 00:00:02.000
o gato

00:00:02.000 --> 00:00:03.000
o gato subiu

00:00:03.000 --> 00:00:04.000
o gato subiu no telhado
"""


def _escrever(tmp_path: Path, conteudo: str) -> Path:
    caminho = tmp_path / "legenda.vtt"
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


def test_parse_extrai_timing_por_palavra(tmp_path):
    cues = parse_vtt(_escrever(tmp_path, VTT_AUTO))

    assert len(cues) == 2
    primeira = cues[0]
    assert primeira.text == "olha isso aqui"
    assert [w.text for w in primeira.words] == ["olha", "isso", "aqui"]
    assert primeira.words[0].start == pytest.approx(5.0)
    assert primeira.words[1].start == pytest.approx(5.4)
    # Cada palavra termina onde a próxima começa.
    assert primeira.words[0].end == pytest.approx(5.4)
    # A última vai até o fim da cue.
    assert primeira.words[-1].end == pytest.approx(7.1)


def test_parse_sem_timing_inline_nao_quebra(tmp_path):
    cues = parse_vtt(_escrever(tmp_path, VTT_SIMPLES))

    assert [c.text for c in cues] == ["primeira linha", "segunda linha"]
    assert cues[0].words == []


def test_dedupe_colapsa_legenda_rolante(tmp_path):
    """Sem isso a legenda queimada aparece triplicada na tela."""
    cues = parse_vtt(_escrever(tmp_path, VTT_ROLANTE))

    assert len(cues) == 1
    assert cues[0].text == "o gato subiu no telhado"
    # O tempo cobre do começo da primeira ao fim da última.
    assert cues[0].start == pytest.approx(1.0)
    assert cues[0].end == pytest.approx(4.0)


def test_slice_rebase_o_tempo_para_zero():
    cues = [
        Cue(10.0, 12.0, "dentro", [Word("dentro", 10.0, 12.0)]),
        Cue(30.0, 32.0, "fora", []),
    ]

    recorte = slice_cues(cues, 9.0, 20.0)

    assert len(recorte) == 1
    assert recorte[0].start == pytest.approx(1.0)
    assert recorte[0].end == pytest.approx(3.0)
    assert recorte[0].words[0].start == pytest.approx(1.0)


def test_slice_corta_cue_que_atravessa_a_borda():
    cues = [Cue(5.0, 25.0, "longa", [])]

    recorte = slice_cues(cues, 10.0, 20.0)

    assert recorte[0].start == pytest.approx(0.0)
    assert recorte[0].end == pytest.approx(10.0)


def test_text_between_junta_apenas_o_intervalo():
    cues = [Cue(0, 2, "um"), Cue(5, 7, "dois"), Cue(50, 52, "tres")]

    assert text_between(cues, 0, 10) == "um dois"


def test_ass_karaoke_gera_um_evento_por_palavra():
    cue = Cue(0.0, 1.5, "um dois tres", [
        Word("um", 0.0, 0.5), Word("dois", 0.5, 1.0), Word("tres", 1.0, 1.5),
    ])

    ass = build_ass([cue], width=1080, height=1920, style="karaoke")

    assert ass.count("Dialogue:") == 3
    # Em cada evento o texto inteiro aparece, com uma palavra destacada.
    assert ass.count("dois") == 3


def test_ass_block_gera_um_evento_por_cue():
    cue = Cue(0.0, 1.5, "um dois tres", [Word("um", 0.0, 1.5)])

    ass = build_ass([cue], width=1080, height=1920, style="block")

    assert ass.count("Dialogue:") == 1


def test_ass_inclui_o_gancho_no_inicio():
    ass = build_ass([], width=1080, height=1920, hook="teste", hook_duration=3.0)

    linha = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "Hook" in linha
    assert "TESTE" in linha
    assert "0:00:00.00,0:00:03.00" in linha


def test_ass_escapa_chaves_para_nao_virar_tag():
    """Chave no texto vira override de estilo no ASS e quebraria a renderização."""
    cue = Cue(0.0, 1.0, "codigo {perigoso}", [])

    ass = build_ass([cue], width=1080, height=1920, style="block")

    assert "{perigoso}" not in ass
    assert "(perigoso)" in ass

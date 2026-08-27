"""Testes do parser de VTT e do gerador de ASS."""

import re
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


# Trecho real de source.pt.vtt, legenda automática do YouTube. A terceira cue
# vai até 00:00:36.430 mas a fala acaba em 00:00:10.040: o VTT rolante segura a
# última linha na tela até a próxima fala começar.
VTT_ROLANTE_REAL = """WEBVTT
Kind: captions
Language: pt

00:00:07.640 --> 00:00:09.790 align:start position:0%

Teremos <00:00:08.048><c>um </c><00:00:08.456><c>amigo </c><00:00:08.864><c>secreto </c><00:00:09.272><c>no </c><00:00:09.680><c>final</c>

00:00:09.790 --> 00:00:09.800 align:start position:0%
Teremos um amigo secreto no final


00:00:09.800 --> 00:00:36.430 align:start position:0%
Teremos um amigo secreto no final
do <00:00:10.040><c>ano.</c>
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


def test_cue_rolante_nao_fica_26s_na_tela(tmp_path):
    """Regressão de material real: a cue ia até 36,4s com a fala acabando em 10,0s.

    Sem teto, a legenda "do ano." ficava parada na tela por 26 segundos — mais
    que o corte inteiro de 30s.
    """
    cues = parse_vtt(_escrever(tmp_path, VTT_ROLANTE_REAL))

    assert len(cues) == 1
    assert cues[0].text.endswith("do ano.")
    # A última palavra começa em 10,04s; o fim da cue acompanha a fala, não a
    # próxima cue lá em 36,43s.
    assert cues[0].end < 13.0


def test_palavra_nao_passa_do_teto_de_exibicao(tmp_path):
    cues = parse_vtt(_escrever(tmp_path, VTT_ROLANTE_REAL))

    duracoes = [w.end - w.start for c in cues for w in c.words]

    assert duracoes
    assert max(duracoes) <= 2.0 + 1e-6


def test_teto_nao_encolhe_legenda_bem_formada(tmp_path):
    """O corte só vale se o timing legítimo passar intacto."""
    cues = parse_vtt(_escrever(tmp_path, VTT_AUTO))

    assert cues[0].end == pytest.approx(7.1)
    assert cues[0].words[-1].end == pytest.approx(7.1)


def test_pausa_longa_no_meio_da_cue_tambem_tem_teto(tmp_path):
    """Duas palavras separadas por 20s de silêncio dentro da mesma cue."""
    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:25.000\n"
        "antes<00:00:24.000><c> depois</c>\n"
    )

    cues = parse_vtt(_escrever(tmp_path, vtt))

    assert cues[0].words[0].end - cues[0].words[0].start <= 2.0 + 1e-6


def test_teto_de_cue_e_configuravel(tmp_path):
    cues = parse_vtt(_escrever(tmp_path, VTT_ROLANTE_REAL), max_word_on_screen=0.5)

    duracoes = [w.end - w.start for c in cues for w in c.words]

    assert max(duracoes) <= 0.5 + 1e-6


def test_cue_sem_timing_de_palavra_tem_teto_de_leitura(tmp_path):
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:40.000\numa linha so\n"

    cues = parse_vtt(_escrever(tmp_path, vtt), max_cue_on_screen=7.0)

    assert cues[0].end == pytest.approx(8.0)


# Legenda automática de duas linhas, como vem do YouTube: a primeira linha de
# cada cue repete a segunda linha da cue anterior. A sobreposição é sufixo ->
# prefixo, e não o prefixo inteiro que o dedupe original cobria.
VTT_DUAS_LINHAS = """WEBVTT

00:01:04.270 --> 00:01:06.910 align:start position:0%
bem-estar deles. Muitas das coisas que faço dependem da

00:01:06.910 --> 00:01:08.590 align:start position:0%
Muitas das coisas que faço dependem da
construção de relacionamentos com as pessoas.

00:01:08.590 --> 00:01:11.030 align:start position:0%
construção de relacionamentos com as pessoas.
Eu me abro mais.
"""


def test_legenda_rolante_de_duas_linhas_nao_repete_a_frase(tmp_path):
    """Regressão de material real: cada frase entrava duas vezes no trecho.

    O dedupe só colapsava prefixo inteiro. Na legenda rolante de duas linhas a
    sobreposição é o fim da cue anterior abrindo a próxima, então tudo passava
    — e dobrava título, descrição e a densidade de fala medida pelo analyzer.
    """
    cues = parse_vtt(_escrever(tmp_path, VTT_DUAS_LINHAS))

    texto = text_between(cues, 0, 200)

    assert texto.count("Muitas das coisas que faço dependem da") == 1
    assert texto.count("construção de relacionamentos com as pessoas.") == 1


def test_trecho_sobreposto_continua_legivel(tmp_path):
    cues = parse_vtt(_escrever(tmp_path, VTT_DUAS_LINHAS))

    texto = text_between(cues, 0, 200)

    assert texto.startswith("bem-estar deles. Muitas das coisas")
    assert texto.endswith("Eu me abro mais.")


def test_sem_sobreposicao_a_cue_passa_intacta(tmp_path):
    cues = parse_vtt(_escrever(tmp_path, VTT_SIMPLES))

    assert [c.text for c in cues] == ["primeira linha", "segunda linha"]


def test_overlap_nao_corta_repeticao_legitima(tmp_path):
    """"que que" repetido de propósito não é sobreposição de cue."""
    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nele disse que\n\n"
        "00:00:03.000 --> 00:00:05.000\nnao era bem assim\n"
    )

    cues = parse_vtt(_escrever(tmp_path, vtt))

    assert [c.text for c in cues] == ["ele disse que", "nao era bem assim"]


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


def _linhas_visiveis(ass: str) -> list[str]:
    """Linhas de texto do ASS, sem as tags de cor e sem os campos do evento."""
    linhas = []
    for evento in (l for l in ass.splitlines() if l.startswith("Dialogue:")):
        texto = evento.split(",", 9)[9]
        for parte in texto.split(r"\N"):
            linhas.append(re.sub(r"\{[^}]*\}", "", parte))
    return linhas


def test_legenda_respeita_a_quebra_configurada():
    """Regressão: a quebra fixa em 26 gerava linha de 1460px numa tela de 1080."""
    cue = Cue(0.0, 3.0, "Para uma equipe dessa magnitude, também é extremamente importante", [])

    ass = build_ass([cue], width=1080, height=1920, style="block", caption_wrap=20)

    excedentes = [l for l in _linhas_visiveis(ass) if len(l) > 20 and " " in l.strip()]
    assert excedentes == []


def test_gancho_respeita_a_quebra_configurada():
    ass = build_ass([], width=1080, height=1920, hook="isso muda tudo agora", hook_wrap=14)

    excedentes = [l for l in _linhas_visiveis(ass) if len(l) > 14 and " " in l.strip()]
    assert excedentes == []


def test_karaoke_tambem_respeita_a_quebra():
    """No karaokê a cue inteira é redesenhada a cada palavra — a quebra vale igual."""
    cue = Cue(0.0, 2.0, "uma frase bem comprida para forçar a quebra", [
        Word(p, i * 0.2, i * 0.2 + 0.2) for i, p in enumerate(
            "uma frase bem comprida para forçar a quebra".split()
        )
    ])

    ass = build_ass([cue], width=1080, height=1920, style="karaoke", caption_wrap=20)

    excedentes = [l for l in _linhas_visiveis(ass) if len(l) > 20 and " " in l.strip()]
    assert excedentes == []


def test_fonte_e_margens_entram_no_cabecalho():
    ass = build_ass([], width=1080, height=1920, font_scale=0.05, margin_h=0.1)

    assert f"Caption,DejaVu Sans,{int(1920 * 0.05)}," in ass
    assert f",{int(1080 * 0.1)},{int(1080 * 0.1)}," in ass


def test_ass_escapa_chaves_para_nao_virar_tag():
    """Chave no texto vira override de estilo no ASS e quebraria a renderização."""
    cue = Cue(0.0, 1.0, "codigo {perigoso}", [])

    ass = build_ass([cue], width=1080, height=1920, style="block")

    assert "{perigoso}" not in ass
    assert "(perigoso)" in ass

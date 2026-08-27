"""Testes de metadados, banco, agendador e configuração."""

from datetime import date

import pytest

from monetizar.analyzer import Segment
from monetizar.channels import Channel, find_channel
from monetizar.config import ClipConfig, Config, PublishConfig
from monetizar.db import Database
from monetizar.downloader import VideoInfo, _parse_rate
from monetizar.metadata import build_metadata
from monetizar.scheduler import Scheduler, deficit


@pytest.fixture
def fonte():
    return VideoInfo(
        video_id="abc123",
        title="Entrevista completa com fulano",
        url="https://www.youtube.com/watch?v=abc123",
        duration=3600.0,
        channel="Canal Origem",
        view_count=1_000_000,
        like_count=50_000,
    )


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "teste.db")


# ------------------------------------------------------------------ metadados


def test_gancho_usa_frase_curta_da_fala(fonte):
    seg = Segment(0, 30, 1.0, text="Isso muda tudo. E o resto vem depois.")

    meta = build_metadata(seg, fonte)

    assert meta.hook == "ISSO MUDA TUDO"


def test_gancho_trunca_quando_nao_ha_pontuacao(fonte):
    """Legenda automática do YouTube não tem pontuação — o texto vira uma frase só."""
    seg = Segment(0, 30, 1.0, text=" ".join(["palavra"] * 40))

    meta = build_metadata(seg, fonte)

    assert len(meta.hook.split()) == 4


def test_gancho_cai_no_template_sem_legenda(fonte):
    seg = Segment(0, 30, 1.0, text="")

    assert build_metadata(seg, fonte).hook


def test_titulo_respeita_o_limite_do_youtube(fonte):
    seg = Segment(0, 30, 1.0, text="palavra " * 100)

    meta = build_metadata(seg, fonte)

    assert len(meta.title) <= 100
    assert meta.title.endswith("#shorts")


def test_descricao_credita_a_fonte(fonte):
    seg = Segment(0, 30, 1.0, text="Um trecho qualquer.")

    meta = build_metadata(seg, fonte, channel_name="Meu Canal")

    assert "Canal Origem" in meta.description
    assert fonte.url in meta.description
    assert "Meu Canal" in meta.description


def test_descricao_sem_credito_quando_desligado(fonte):
    seg = Segment(0, 30, 1.0, text="Um trecho qualquer.")

    meta = build_metadata(seg, fonte, credit=False)

    assert "Canal Origem" not in meta.description


def test_hashtags_juntam_fixas_e_da_fala(fonte):
    seg = Segment(0, 30, 1.0, text="investimento investimento investimento renda")

    meta = build_metadata(seg, fonte, base_hashtags=["#financas"])

    assert "#financas" in meta.hashtags
    assert "#shorts" in meta.hashtags
    assert "#investimento" in meta.hashtags
    assert len(meta.hashtags) <= 12


def test_caption_junta_descricao_e_hashtags(fonte):
    seg = Segment(0, 30, 1.0, text="Alguma fala.")

    caption = build_metadata(seg, fonte, base_hashtags=["#teste"]).caption

    assert "#teste" in caption
    assert len(caption) <= 2200


# ---------------------------------------------------------------------- banco


def test_source_registrado_nao_repete(db, fonte):
    assert not db.has_source("abc123")

    db.add_source(fonte)

    assert db.has_source("abc123")


def test_clip_exists_evita_regerar(db, fonte):
    db.add_source(fonte)
    db.add_clip(
        source_id="abc123", channel_slug="c1", path="/tmp/x.mp4",
        start_s=0, end_s=30, score=1.0, title="t", description="d",
        hashtags=["#a"], hook="H",
    )

    assert db.clip_exists("/tmp/x.mp4")
    assert not db.clip_exists("/tmp/y.mp4")


def test_ready_clips_ignora_o_que_ja_esta_na_fila(db, fonte):
    db.add_source(fonte)
    clip_id = db.add_clip(
        source_id="abc123", channel_slug="c1", path="/tmp/x.mp4",
        start_s=0, end_s=30, score=1.0, title="t", description="d",
        hashtags=[], hook="H",
    )
    assert len(db.ready_clips("c1")) == 1

    db.schedule_clip(clip_id, "c1", "youtube", "2026-01-01T08:00:00")

    assert db.ready_clips("c1") == []


def test_schedule_nao_duplica_a_mesma_plataforma(db, fonte):
    db.add_source(fonte)
    clip_id = db.add_clip(
        source_id="abc123", channel_slug="c1", path="/tmp/x.mp4",
        start_s=0, end_s=30, score=1.0, title="t", description="d",
        hashtags=[], hook="H",
    )

    assert db.schedule_clip(clip_id, "c1", "youtube", "2026-01-01T08:00:00")
    assert not db.schedule_clip(clip_id, "c1", "youtube", "2026-01-02T08:00:00")
    # Outra plataforma é um post distinto e pode repetir o mesmo corte.
    assert db.schedule_clip(clip_id, "c1", "tiktok", "2026-01-01T08:00:00")


def test_mark_posted_tira_da_fila(db, fonte):
    db.add_source(fonte)
    clip_id = db.add_clip(
        source_id="abc123", channel_slug="c1", path="/tmp/x.mp4",
        start_s=0, end_s=30, score=1.0, title="t", description="d",
        hashtags=[], hook="H",
    )
    db.schedule_clip(clip_id, "c1", "youtube", "2026-01-01T08:00:00")
    pendentes = db.pending_schedule("c1")

    db.mark_posted(pendentes[0]["id"], note="ok")

    assert db.pending_schedule("c1") == []
    assert db.stats()["posted"] == 1


# ------------------------------------------------------------------ agendador


def _canal(**kwargs):
    base = dict(slug="c1", name="Canal", queries=["algo"], platforms=["youtube"])
    return Channel(**{**base, **kwargs})


def test_plan_distribui_nos_horarios_configurados(db, fonte):
    db.add_source(fonte)
    for i in range(3):
        db.add_clip(
            source_id="abc123", channel_slug="c1", path=f"/tmp/{i}.mp4",
            start_s=0, end_s=30, score=1.0, title=f"t{i}", description="d",
            hashtags=[], hook="H",
        )
    publish = PublishConfig(videos_per_day=2, slots=["08:00", "20:00"], buffer_days=2)
    scheduler = Scheduler(db, publish)

    posts = scheduler.plan(_canal(), days=2, start_day=date(2099, 1, 1))

    assert len(posts) == 3
    # Ordenados no tempo e dentro dos horários configurados.
    horas = [p.publish_at.hour for p in posts]
    assert horas == sorted(horas) or set(horas) <= {8, 20}
    assert all(p.publish_at.hour in (8, 20) for p in posts)


def test_plan_agenda_em_todas_as_plataformas_do_canal(db, fonte):
    db.add_source(fonte)
    db.add_clip(
        source_id="abc123", channel_slug="c1", path="/tmp/x.mp4",
        start_s=0, end_s=30, score=1.0, title="t", description="d",
        hashtags=[], hook="H",
    )
    scheduler = Scheduler(db, PublishConfig(videos_per_day=1, slots=["08:00"]))

    posts = scheduler.plan(
        _canal(platforms=["youtube", "tiktok"]), days=1, start_day=date(2099, 1, 1)
    )

    assert {p.platform for p in posts} == {"youtube", "tiktok"}


def test_plan_sem_cortes_nao_agenda_nada(db):
    scheduler = Scheduler(db, PublishConfig())

    assert scheduler.plan(_canal(), days=3, start_day=date(2099, 1, 1)) == []


def test_deficit_mede_o_que_falta_produzir(db):
    publish = PublishConfig(videos_per_day=5, buffer_days=3)

    faltas = deficit([_canal()], db, publish)

    assert faltas["c1"] == 15


# --------------------------------------------------------------------- canais


def test_canal_exige_uma_fonte():
    with pytest.raises(ValueError, match="queries.*sources"):
        Channel(slug="c1", name="Canal")


def test_canal_rejeita_perfil_desconhecido():
    with pytest.raises(ValueError, match="duration_profile"):
        _canal(duration_profile="inexistente")


def test_perfil_tiktok_rewards_exige_mais_de_60s():
    """O Creator Rewards do TikTok só paga em vídeos de 60s ou mais."""
    clip = ClipConfig()

    _canal(duration_profile="tiktok_rewards").apply_profile(clip)

    assert clip.min_duration > 60.0
    assert clip.target_duration > 60.0


def test_perfil_shorts_fica_abaixo_de_60s():
    clip = ClipConfig()

    _canal(duration_profile="shorts").apply_profile(clip)

    assert clip.max_duration < 60.0


def test_find_channel_lista_os_conhecidos_no_erro():
    with pytest.raises(KeyError, match="c1"):
        find_channel([_canal()], "inexistente")


# --------------------------------------------------------------- configuração


def test_config_rejeita_alvo_fora_da_faixa():
    cfg = Config()
    cfg.clip.target_duration = 200.0

    with pytest.raises(ValueError, match="target_duration"):
        cfg.validate()


def test_config_rejeita_slots_insuficientes():
    cfg = Config()
    cfg.publish.videos_per_day = 5
    cfg.publish.slots = ["08:00"]

    with pytest.raises(ValueError, match="slots"):
        cfg.validate()


def test_config_exemplo_carrega_e_valida():
    """O arquivo versionado precisa funcionar sem edição."""
    cfg = Config.load()

    assert cfg.publish.videos_per_day == 5
    assert cfg.clip.height == 1920


@pytest.mark.parametrize(
    "entrada,esperado",
    [("5M", 5 * 1024**2), ("500K", 500 * 1024), ("1000", 1000), (None, None)],
)
def test_parse_rate(entrada, esperado):
    assert _parse_rate(entrada) == esperado


def test_engagement_rate_sem_views_nao_divide_por_zero():
    assert VideoInfo("x", "t", "u", 60.0, view_count=0, like_count=5).engagement_rate == 0.0

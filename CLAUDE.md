# Monetizar Canais — contexto para o agente

Pipeline de cortes verticais: yt-dlp acha e baixa o vídeo, o analisador escolhe
os melhores momentos, o ffmpeg corta em 9:16 com legenda animada, e o scheduler
monta uma fila de 5 posts/dia. Objetivo do dono: monetizar por views no YouTube
Shorts e no TikTok, escalando de 1 para 20 canais.

## Estado atual

O pipeline está **implementado e testado**, com uma exceção importante:

> ⚠️ **O caminho do yt-dlp nunca foi exercitado com tráfego real.**
> A sessão que escreveu o código rodava num ambiente cujo proxy bloqueava
> `www.youtube.com:443` (403 no CONNECT, política de egress "Trusted").
> `src/monetizar/downloader.py` compila e passa nos testes de unidade, mas
> `search`, `download` e o `run` completo nunca tocaram o YouTube de verdade.

Tudo o mais foi verificado de ponta a ponta: 50 testes passam, e a renderização
foi validada com vídeo sintético (1080x1920, fundo desfocado, gancho nos 3s,
legenda karaokê palavra a palavra, áudio a -14 LUFS).

## Primeira coisa a fazer numa sessão nova

Confirme se a rede já libera o YouTube:

```bash
curl -sS "$HTTPS_PROXY/__agentproxy/status" | head -20   # ver bloqueios recentes
monetizar search "podcast entrevista" -n 5               # teste real
```

- **Se funcionar:** rode `monetizar run --dry-run`, depois `monetizar run` com
  uma fonte legítima, e corrija o que aparecer. É exatamente o trabalho pendente.
- **Se der 403:** o ambiente ainda está em `Trusted`. Não tente contornar o
  proxy, não desabilite TLS. Avise o dono: em claude.ai/code → ícone de nuvem
  → engrenagem do ambiente → **Network access** → `Full`, ou `Custom` com
  `*.youtube.com`, `youtube.com`, `*.googlevideo.com`, `*.ytimg.com`,
  `*.ggpht.com` (o `*.googlevideo.com` é obrigatório — é de onde vêm os bytes
  do vídeo). A mudança só vale em sessão nova.

## Setup do ambiente

`ffmpeg` é obrigatório e **não** vem instalado:

```bash
sudo apt-get update -qq && sudo apt-get install -y ffmpeg
pip install -e ".[dev]"
pytest                                    # 50 testes, ~0.4s
```

O `apt-get update` reclama de um PPA de PHP bloqueado pelo proxy — é ruído,
o ffmpeg instala normalmente.

Os `config/*.example.yaml` funcionam sem edição, então dá para testar antes de
configurar qualquer coisa.

## Decisões que já foram tomadas — não desfaça sem conversar

**O pipeline não publica sozinho.** Ele vai até `data/clips/<slug>/manifest.json`,
que é a fila de revisão humana. Isso é deliberado: a política de conteúdo
inautêntico do YouTube avalia o canal inteiro, e publicação automática sem
revisão é o padrão exato que ela procura. Se pedirem upload automático, implemente
— mas explique o risco antes.

**Perfis de duração por canal.** `shorts` (15-59s) e `tiktok_rewards` (60s+).
Existem porque o Creator Rewards do TikTok **não paga vídeos abaixo de 1 minuto**.
Um corte de 30s pode viralizar lá e render zero.

**`max_clips_per_source: 3`.** Limite de política, não de estética. Muitos cortes
do mesmo vídeo é literalmente "uploads repetitivos com pouca variação".

**Uma fonte usada nunca volta.** `Pipeline.discover` filtra por `db.has_source`.
Evita o mesmo material aparecer em canais diferentes.

**O corte alinha com o início de uma fala** (`analyzer._snap`) e mira a duração
alvo, não o máximo. Cortar no meio de uma palavra derruba os 3 primeiros
segundos, que é onde 50-60% do público desiste.

## Contexto de negócio

`docs/MONETIZACAO.md` tem a pesquisa com fontes. Os três pontos que mudam
qualquer decisão de produto:

1. TikTok não paga vídeo com menos de 60s. RPM lá é ~10x o do Shorts
   (US$ 0,40-2,00 contra US$ 0,01-0,17).
2. Shorts exige 10M de views em 90 dias para entrar no YPP — dobra para 20M
   em 2027.
3. Em janeiro de 2026 o YouTube apagou 4,7 bilhões de views numa única onda de
   aplicação da política de conteúdo inautêntico. Avaliação é do canal inteiro.

`docs/PLAYBOOK.md` tem o tático de retenção e a ordem de escalar 1 → 3 → 20.

Ao falar de monetização, seja direto sobre os números. O dono pediu explicitamente
pesquisa sobre isso e prefere o dado real ao otimismo.

## Convenções do código

- Código, comentários, docs e mensagens de commit em **português**.
- Comentários explicam **por quê**, não o quê. Vários codificam uma regra de
  plataforma (duração, RPM, política) — preserve essa informação ao editar.
- Testes em `tests/`, nomes descritivos em português, sem mocks de rede.
- `Config` e `Channel` validam na carga e levantam `ValueError` com mensagem
  útil. Mantenha esse padrão ao adicionar campo novo.

## Branch

Trabalho em `claude/ytdlp-video-editor-nwuhqg`. Se o PR dessa branch já tiver
sido mergeado, recomece a partir da branch padrão em vez de empilhar commits.

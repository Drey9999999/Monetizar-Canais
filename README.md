# Monetizar Canais

Pipeline de cortes verticais para YouTube Shorts e TikTok: acha o vídeo com
yt-dlp, escolhe os melhores momentos, corta em 9:16 com legenda animada e
organiza a fila de publicação.

```
buscar → baixar → analisar → cortar → legendar → agendar → [você revisa] → postar
```

---

## Leia isto antes de escalar

A pesquisa de monetização está em **[docs/MONETIZACAO.md](docs/MONETIZACAO.md)** e
muda o plano em três pontos:

1. **O TikTok não paga por vídeo com menos de 60 segundos.** Um corte de 30s pode
   viralizar lá e render zero. Receita por view no TikTok só existe em vídeos de
   1 minuto ou mais — e o RPM lá é ~10x o do Shorts.
2. **O Shorts paga muito pouco.** RPM de US$ 0,01–0,17 por 1.000 views, e a
   entrada exige 10 milhões de views em 90 dias (dobra para 20 milhões em 2027).
3. **Publicar cortes em massa é o padrão que as duas plataformas passaram 2025 e
   2026 aprendendo a detectar.** Em janeiro de 2026 o YouTube apagou 4,7 bilhões
   de views numa única onda de aplicação da política de conteúdo inautêntico — e
   a avaliação é do **canal inteiro**, não vídeo a vídeo.

Isso não inviabiliza a operação. Significa que o que decide o resultado não é o
volume de cortes, e sim **a transformação que você acrescenta a cada um** e o
**direito de usar a fonte**. O pipeline faz o trabalho pesado até a fila de
revisão; a camada de transformação é sua. O tático de retenção e a ordem correta
de escalar 1 → 3 → 20 canais estão em **[docs/PLAYBOOK.md](docs/PLAYBOOK.md)**.

---

## Instalação

Requer Python 3.11+ e **ffmpeg**.

```bash
sudo apt install ffmpeg          # Ubuntu/Debian
brew install ffmpeg              # macOS

git clone https://github.com/Drey9999999/Monetizar-Canais.git
cd Monetizar-Canais
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Configuração:

```bash
cp config/config.example.yaml   config/config.yaml
cp config/channels.example.yaml config/channels.yaml
```

Os dois arquivos `.yaml` reais são ignorados pelo git. Os `.example` funcionam
sem edição, então dá para testar antes de configurar.

---

## Uso

### Navegar sem baixar nada

```bash
monetizar search "podcast brasileiro entrevista" -n 10
monetizar search "https://www.youtube.com/@umcanal/videos" -n 20
```

Mostra id, duração, views e engajamento (likes/views) já filtrados pela faixa de
duração configurada.

### Baixar um vídeo com legenda

```bash
monetizar download "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Cortar um arquivo local

```bash
# Cortes automáticos: o analisador escolhe os melhores momentos
monetizar clip data/downloads/VIDEO_ID/source.mp4 \
  --subtitles data/downloads/VIDEO_ID/source.pt.vtt -c 3

# Corte manual, quando você já sabe onde está o momento
monetizar clip source.mp4 --start 412 --end 448
```

### Pipeline completo

```bash
monetizar run --dry-run              # só lista os candidatos
monetizar run -c cortes-podcast-br   # um canal
monetizar run                        # todos os canais ativos
```

Cada canal recebe um `data/clips/<slug>/manifest.json` com os cortes, ganchos,
títulos, descrições e hashtags gerados. **Esse arquivo é a fila de revisão.**

### Agendar e acompanhar

```bash
monetizar schedule        # distribui os cortes prontos em 5 posts/dia
monetizar queue           # o que está agendado, pronto para subir
monetizar status          # estoque, fila e déficit por canal
```

`status` responde a pergunta da operação diária: onde o estoque acaba primeiro.

```
--- canais (3 ativos) ---
  OK cortes-podcast-br    fila  30 (3d)  prontos   4  faltam   0
  !! financas-shorts      fila   8 (0d)  prontos   1  faltam   6
```

---

## Como os cortes são escolhidos

O `analyzer` combina três sinais baratos, normalizados na mesma escala:

| Sinal | Peso | O que captura |
|---|---|---|
| Energia do áudio (RMS) | 1.0 | ênfase, risada, reação |
| Palavras-chave da legenda | 1.5 | "o segredo", "ninguém te conta", "o erro" |
| Densidade de fala | 0.8 | trecho denso prende mais que silêncio |

Depois, dois ajustes que importam mais do que parecem:

- **Alinhamento com a fala.** O corte começa no início de uma frase, não no meio
  de uma palavra — a forma mais rápida de perder o espectador nos 3 primeiros
  segundos.
- **Distância mínima entre cortes.** Dois cortes colados do mesmo vídeo geram
  clipes quase idênticos, que é exatamente o que a política de conteúdo
  repetitivo procura.

A lista de `keywords` no `config.yaml` é a alavanca mais barata para melhorar a
qualidade dos cortes. Ajuste ao seu nicho.

---

## Como os cortes são editados

- **9:16 em 1080x1920.** Três modos: `blur` (fundo desfocado, preserva o
  enquadramento original), `crop` (corte central) ou `pad` (barras pretas).
- **Legenda karaokê palavra a palavra.** As legendas automáticas do YouTube
  trazem o timing de cada palavra embutido no VTT, então dá para animar sem rodar
  transcrição própria. 85% dos Shorts começam sem som — sem legenda, o gancho não
  é entregue.
- **Gancho sobreposto** nos primeiros 3 segundos, tirado da própria fala.
- **Áudio normalizado a -14 LUFS**, o alvo que as plataformas usam. Entregar já
  nesse nível evita a plataforma abaixar o volume do corte.

---

## Perfis de duração

Definido por canal em `channels.yaml`:

| Perfil | Duração | Para quê |
|---|---|---|
| `shorts` | 15–59s (alvo 30s) | YouTube Shorts, Reels, TikTok orgânico |
| `tiktok_rewards` | 62–180s (alvo 75s) | **Único formato que o Creator Rewards paga** |

---

## Estrutura

```
src/monetizar/
  cli.py          comandos: search, download, clip, run, schedule, queue, status
  config.py       config.yaml → dataclasses validadas
  channels.py     definição dos canais e perfis de duração
  downloader.py   yt-dlp: busca, filtro, download, legendas
  captions.py     VTT com timing de palavra → ASS animado
  analyzer.py     escolha dos melhores momentos
  editor.py       ffmpeg: corte, 9:16, legenda queimada, loudness
  metadata.py     gancho, título, descrição, hashtags
  pipeline.py     orquestração + manifest.json
  scheduler.py    fila de 5 posts/dia com buffer
  db.py           SQLite: dedupe de fontes, cortes e agenda
```

Estado em `data/` (ignorado pelo git): `downloads/`, `clips/<slug>/`,
`monetizar.db`.

---

## Testes

```bash
pip install -e ".[dev]"
pytest
```

---

## Publicação

O pipeline vai até a fila — **não publica sozinho**, por escolha. Publicar
automaticamente sem revisão é o caminho direto para a política de conteúdo
inautêntico, que avalia o canal inteiro. Use `monetizar queue` para revisar e
subir.

Se depois quiser automatizar o upload, os pontos de integração são a YouTube Data
API v3 (`videos.insert`) e a TikTok Content Posting API — ambas exigem app
aprovado e OAuth por canal.

---

## Aviso

Cortar vídeo de terceiros e republicar é reprodução de obra protegida. Use
conteúdo próprio, licenciado, com parceria explícita, Creative Commons ou em
domínio público. Creditar na descrição ajuda na avaliação de conteúdo
reutilizado, mas **não substitui autorização**. Detalhes em
[docs/MONETIZACAO.md](docs/MONETIZACAO.md#4-direito-de-uso-do-material).

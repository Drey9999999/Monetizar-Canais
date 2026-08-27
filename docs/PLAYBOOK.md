# Playbook de retenção e viralização

Números de 2026. Fontes no fim. Este documento é sobre **o que faz o vídeo ser
distribuído** — a parte que o pipeline não decide por você.

---

## 1. A regra que domina todas as outras

> **50–60% de quem desiste do vídeo desiste nos primeiros 3 segundos.**

E o corte de distribuição é explícito: se a taxa de swipe-away passa de ~40% na
primeira hora, o algoritmo **para de distribuir**. Se o grupo de teste inicial
desliza fora a 40–50% nos primeiros 1–3 segundos, a distribuição pode parar por
completo.

Tudo abaixo é consequência disso.

---

## 2. Os primeiros 3 segundos

O que funciona, em ordem de eficácia medida:

| Estrutura | Como soa | Por que funciona |
|---|---|---|
| **Afirmação forte** | "Você está perdendo dinheiro com isso todo mês." | Sinaliza valor imediato |
| **Lacuna de curiosidade** | "Ninguém te contou o que acontece depois disso." | Cria coceira que precisa ser coçada |
| **Pergunta direta** | "Por que você ainda faz isso?" | Interpela o espectador pessoalmente |
| **Surpresa visual** | Movimento, corte brusco, algo fora do lugar | Quebra o padrão de scroll |

Erros que matam o vídeo antes de começar:

- Logo, vinheta ou "fala galera" na abertura. Você tem 3 segundos e gastou 2.
- Contexto antes do gancho. O contexto vem **depois** de prender.
- Começar no meio de uma palavra. (O pipeline evita isso alinhando o corte com o
  início de uma fala — veja `analyzer._snap`.)

---

## 3. Legenda não é acessibilidade, é retenção

> **85% dos Shorts são assistidos sem som no início.**

Sem legenda, o gancho simplesmente não é entregue. E o formato importa: legenda
**palavra a palavra**, onde cada palavra aparece ou acende conforme é falada,
cria um ritmo visual que segura atenção **independentemente do áudio**.

É por isso que `caption_style: karaoke` é o padrão do projeto, e por que o
pipeline prefere legendas automáticas do YouTube — elas trazem o timing de cada
palavra embutido.

---

## 4. A curva de retenção

Três formatos de curva prevêem quase todos os resultados:

| Curva | Formato | Resultado |
|---|---|---|
| **Penhasco** | Queda de 30–50% nos primeiros 3s | Mata o alcance |
| **Corcova** | Sustenta acima de 60% no miolo | Alta taxa de compartilhamento |
| **Platô** | Estável acima de 70%, com final que dá loop | **A favorita do algoritmo** |

O platô é o alvo. Como se constrói um:

- **Sem gordura.** Todo segundo que não avança a ideia é um convite a deslizar.
- **Final que fecha em loop.** Quando o fim conecta com o começo, o espectador
  reassiste sem perceber — e o tempo de exibição dobra no mesmo vídeo.
- **Sem despedida.** "Valeu pessoal, até a próxima" é uma instrução explícita
  para sair. Corte no ponto alto.

**Duração ideal: 20–35 segundos** para Shorts. O algoritmo favorece
particularmente a faixa de 20–25s.

⚠️ Mas lembre: no TikTok, **abaixo de 60s não existe pagamento**. Se o objetivo é
receita por view via Creator Rewards, o alvo muda para 60–90s, e a curva de
retenção precisa aguentar o dobro do tempo. Use `duration_profile: tiktok_rewards`.

---

## 5. Operação: 5 vídeos por dia

### Horários

O padrão do projeto (`08:00, 12:00, 15:00, 18:00, 21:00`) espalha as publicações
pelo dia ativo. Não é sagrado — depois de 2 semanas, olhe o relatório de horário
do YouTube Studio e do TikTok Analytics e mova os slots para onde **seu** público
está. Os dados do seu canal valem mais que qualquer horário genérico.

### Consistência

O algoritmo aprende o ritmo do canal. Cinco vídeos por dia com buracos aleatórios
performa pior que três por dia todos os dias. Por isso o pipeline mantém um
**buffer de 3 dias** (`publish.buffer_days`): o estoque absorve o dia em que nada
dá certo.

Rode `monetizar status` toda manhã. Ele responde a única pergunta que importa na
operação diária: **onde o estoque vai acabar primeiro.**

### Variedade dentro do canal

`max_clips_per_source: 3` existe por um motivo de política, não de estética. Dez
cortes do mesmo vídeo, publicados em sequência, são literalmente a definição de
"uploads repetitivos com pouca variação" — a primeira das três categorias
inelegíveis para monetização. Três é um teto conservador; considere dois.

---

## 6. Escalar para 20 canais — a ordem correta

A tentação é abrir os 20 e deixar rodando. Os números dizem para não fazer isso.

**Fase 1 — Um canal, 30 dias.** Publique 5/dia. Não mexa em nada além do gancho.
Meça: retenção nos 3s, retenção média, taxa de compartilhamento.

Critério de saída: retenção média acima de 60% e pelo menos um vídeo passando de
50 mil views. Se não bater, **o problema não é o volume** — é o nicho, a fonte ou
o gancho. Abrir mais canais multiplica o erro.

**Fase 2 — Três canais, 60 dias.** Nichos **diferentes** e fontes **diferentes**.
Mesmo processo, temas distintos. Isso testa se o resultado da Fase 1 foi o método
ou foi sorte com um nicho específico.

Critério de saída: pelo menos dois dos três repetindo o resultado da Fase 1.

**Fase 3 — Escalar até 20.** Só aqui. E com uma regra que vale mais que todas as
outras:

> **Não use o mesmo template visual em todos os canais.**
>
> A avaliação de conteúdo inautêntico olha o canal como um todo — tema, vídeos
> mais vistos, uploads recentes, metadados. Vinte canais com o mesmo fundo
> desfocado, a mesma fonte amarela e a mesma estrutura de descrição são um
> padrão trivial de detectar, e a aplicação vem para todos de uma vez.

Cada canal precisa de: fonte própria, estilo de legenda próprio, estrutura de
descrição própria e uma tese editorial própria. Isso é trabalho humano, e é
exatamente o trabalho que as políticas de 2026 foram desenhadas para exigir.

Concretamente, o que **não** compartilhar entre canais:

- A mesma fonte de vídeo (o pipeline já bloqueia isso: uma fonte usada não volta)
- O mesmo `reframe`, a mesma cor de destaque, a mesma fonte tipográfica
- O mesmo conjunto de hashtags fixas
- O mesmo texto de rodapé de descrição

---

## 7. Checklist antes de publicar

Rode `monetizar queue` e, para cada corte:

- [ ] O gancho está nos primeiros 3 segundos e é legível sem som?
- [ ] O vídeo começa no início de uma fala, não no meio de uma palavra?
- [ ] Tem alguma gordura nos primeiros 5 segundos? Corte.
- [ ] O final dá loop ou tem despedida? Corte a despedida.
- [ ] **Existe transformação minha aqui** — comentário, contexto, edição que
      muda a leitura? Se a resposta for não, não publique: leia
      [MONETIZACAO.md](MONETIZACAO.md#3-o-risco-que-mata-a-operação-de-20-canais).
- [ ] **Eu tenho direito de usar esta fonte?** Conteúdo próprio, licença,
      parceria, CC ou domínio público.
- [ ] Duração bate com o destino? (Shorts: 20–35s. TikTok Rewards: 60s+.)

---

## 8. O que medir, semana a semana

| Métrica | Onde | Alvo | O que fazer se falhar |
|---|---|---|---|
| Retenção aos 3s | YT Studio / TikTok Analytics | > 70% | Refazer o gancho. Só o gancho. |
| Retenção média | idem | > 60% | Encurtar. Cortar a gordura do miolo. |
| Swipe-away 1ª hora | TikTok Analytics | < 40% | O gancho não combina com o conteúdo |
| Compartilhamentos | idem | subindo | Sinal de "corcova" — repita o formato |
| Inscritos por 1k views | YT Studio | subindo | Se cair, o canal não tem tese clara |

Mude **uma variável por vez**. Trocar gancho, duração e horário na mesma semana
não gera aprendizado nenhum — gera ruído.

---

## Fontes

- [Hook formulas that drive 3-second holds (OpusClip)](https://www.opus.pro/blog/youtube-shorts-hook-formulas)
- [The first 3 seconds: hook structures that stop scroll](https://virvid.ai/blog/first-3-seconds-hook-faceless-shorts-2026)
- [The YouTube Shorts retention curve playbook (2026)](https://aibrify.com/blog/youtube-shorts-retention-curve-playbook)
- [The ideal YouTube Shorts length & format for retention](https://www.opus.pro/blog/ideal-youtube-shorts-length-format-retention)
- [The YouTube Shorts algorithm decoded (2026)](https://www.clipspeed.ai/blog/youtube-shorts-algorithm-decoded.html)
- [YouTube Inauthentic Content Policy 2026](https://www.auditsocials.com/blog/youtube-inauthentic-content-policy-2026-mass-produced-ai-generated-monetization-creators-brands)
- [Understanding TikTok's Originality Policy](https://www.tiktok.com/creator-academy/article/tiktok-originality-policy)

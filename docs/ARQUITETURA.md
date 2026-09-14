# Arquitetura do Project MOIRAI

## Origem

Extraído de `Project G.A.I.A/assistant/features/anime_tracker/anime_tracker.py`
em 2026-08-24 (Fase 1 da migração "Assistente de Animes → Project MOIRAI",
ver `Project G.A.I.A/assistant/docs/TODO.md` -> "Arquitetura do ecossistema").
Mesmo motivo dos outros satélites (Argus, IRIS): a feature não tem nenhuma
IA/persona no caminho crítico (scraping + estado + qBittorrent, tudo
determinístico), então não faz sentido morar dentro do processo da GAIA -
o único valor de "estar dentro" seria conveniência, e essa conveniência já
tem custo real (a GAIA precisa estar de pé pra qualquer coisa relacionada a
anime acontecer, incluindo os downloads).

## Estrutura

```
moirai/
├── main.py              # entry point, guarda de instância única (porta 8769),
│                         # loop de manutenção (5min: downloads/biblioteca/MAL)
├── api_bridge.py         # ponte HTTP (porta 8768) - ver contrato completo lá
├── config.py             # config persistida (data/moirai_config.json) -
│                         # equivalente local do que eram getters/setters de
│                         # brain_store.py na GAIA antes da extração
├── core/
│   └── anime_tracker.py  # motor completo (scraping/estado/download/MAL/AniList) -
│                         # movido quase sem alteração (só os imports do topo)
└── integrations/
    ├── myanimelist/mal_client.py   # movido verbatim (já era self-contained)
    └── anilist/anilist_client.py   # movido verbatim (já era self-contained)
```

## Por que foi fácil mover `anime_tracker.py` quase sem alteração

O arquivo original só dependia de 3 coisas de fora dele mesmo:
`integrations.myanimelist.mal_client`, `integrations.anilist.anilist_client`
(ambos zero dependência de GAIA - só `requests`) e 14 pares de getter/setter
de `brain_store.py` (config persistida em `brain.json`, sempre lida direto
do disco a cada chamada, sem cache em memória). Bastou recriar essas 14
funções com o MESMO NOME num `config.py` próprio (`data/moirai_config.json`)
e trocar as 3 linhas de import no topo do arquivo - o corpo inteiro (2200+
linhas) não precisou de nenhuma outra mudança.

## Relação com os outros processos (padrão "GAIA → satélite (poll)" +
## "IRIS → satélite (ação direta)", ver TODO.md da GAIA)

- **IRIS → MOIRAI (ação direta, sem IA no meio)**: plugin `iris_plugin_moirai`
  (repo do IRIS) fala com `GET /anime/para_assistir` (2026-08-24 - só quem
  já tem episódio baixado pronto, com `chave`/`capa_url` junto pro IRIS
  cachear a capa como ícone; ver `obter_titulos_para_assistir` abaixo),
  `GET /anime/capa/<chave>?url=...`, `POST /anime/adicionar`, `POST /anime/
  assistir/<titulo>` - a categoria "🎬 Anime Tracker" do popup só aparece se
  o MOIRAI estiver respondendo (TCP connect simples, nunca cacheado).
- **GAIA → MOIRAI (poll, 1x/dia)**: o Agendador Diário da GAIA consulta
  `GET /checagem_diaria` na hora configurada - a GAIA decide QUANDO
  perguntar (fila/lock com os outros avisos proativos) e O QUE DIZER no
  Discord/voz (valor de persona); o MOIRAI só devolve o dado bruto já
  formatado em texto (mas nunca envia nada sozinho).
- **GAIA → MOIRAI (comandos explícitos)**: `core/agent/comandos.py`
  ("/adicionar_anime", "/verificar_animes", "/status_anime",
  "/progresso_anime") e o "agente leve"/ferramentas de recomendação
  (`core/agent/turno.py`, `core/tools/handlers.py`) consultam vários
  endpoints de leitura (`/anime/titulos_e_chaves`, `/anime/
  animes_rastreados`, `/anime/estados_lancamento_anilist`, `/mal/*`).
- **MOIRAI → GAIA (webhook, único caso de mão inversa)**: quando
  `sincronizar_biblioteca_local` detecta um episódio movido pra pasta de
  assistidos sozinho, o MOIRAI avisa a GAIA por `POST /moirai/
  episodio_assistido` (`MOIRAI_GAIA_WEBHOOK_URL`) - antes da extração, isso
  era uma chamada de callback Python direta (mesmo processo); agora
  precisa ser HTTP porque são processos separados. Silencioso se a GAIA não
  estiver rodando (nunca trava o MOIRAI esperando por um aviso que ninguém
  vai ouvir).

## Fase 2 (2026-08-24) - UI rica migrada

A UI rica do Painel da GAIA (`ui/qt_modais/animes.py`, ~1100 linhas -
abas/sub-abas, agrupamento por temporada, edição manual de episódios,
casamento manual com MAL) foi reescrita como cliente HTTP - o botão "🎬
Assistente de Animes" do Painel voltou a funcionar, sem mudança nenhuma
visível pro usuário. Endpoints novos em `moirai/api_bridge.py`:
`marcar_interesse`, `remover`, `baixar_pendentes`, `baixar_episodios_
selecionados`, `definir_ultimo_lancado/baixado/assistido`, `renomear_
biblioteca`, `sincronizar_biblioteca`, `assistir_chave/<chave>`,
`temporada_atual`, `capa/<chave>` (bytes, ver abaixo), config genérico
(`GET`/`POST /config`) e os 4 endpoints `/mal/*` de casamento manual.

**2 mudanças de contrato reais** (não só troca de import - a mudança de
processo exigia):
- **Capa do anime**: `capa_local_cacheada`/`obter_capa_local` (devolviam
  caminho de arquivo local - sem sentido do lado da GAIA, que não tem
  acesso ao disco do MOIRAI) viraram `GET /anime/capa/<chave>?url=...`,
  que devolve os BYTES da imagem direto (`QPixmap.loadFromData()` no
  cliente, sem nenhum arquivo local necessário do lado de quem consome).
- **Assistir episódio**: `assistir_e_monitorar` (abria o player E
  monitorava a janela pra saber quando terminou, na mesma chamada de quem
  pedia) virou `POST /anime/assistir_chave/<chave>` - o MOIRAI resolve o
  episódio, abre o player e monitora TUDO no próprio processo dele (é
  quem tem acesso ao disco de downloads/player local), devolvendo só
  sucesso/erro. Efeito colateral aceito: o card no Painel da GAIA não se
  auto-atualiza mais sozinho quando termina de assistir (o aviso "movido
  pra pasta de assistidos" continua chegando pelo Discord, via o webhook
  de sempre - `POST /moirai/episodio_assistido`).
- `POST /anime/adicionar` também mudou - não dispara mais o download
  sozinho (isso virou `POST /anime/baixar_pendentes`, uma chamada
  separada), pra permitir que a UI rica decida entre baixar tudo pendente
  ou abrir o seletor de episódios (`baixar_episodios_selecionados`) antes
  de disparar qualquer coisa.

Validado de ponta a ponta com dados reais: ~90 animes rastreados, capa
real baixada como bytes (23KB), roundtrip de config testado, `renomear_
biblioteca` (dry-run) confirmado seguro.

## Anime Tracker do IRIS: só "Para assistir" + capa (2026-08-24, mesmo dia)

Pedido do usuário: a lista da categoria "🎬 Anime Tracker" do Menu Radial
(IRIS) estava mostrando TODO "tenho_interesse", inclusive quem ainda não
tinha nada baixado - clicar num desses era um clique morto (o IRIS não tem
seletor de episódio, só abre o 1º baixado direto). `obter_titulos_para_
assistir` (`moirai/core/anime_tracker.py`) reaproveita `tem_episodio_
disponivel_para_assistir` (já existia, usada pela aba "▶️ Disponíveis" do
Painel) pra filtrar só quem tem episódio `"baixado"` de verdade, e devolve
`chave`/`capa_url` junto (não só o título) - novo endpoint `GET /anime/
para_assistir`. O download/cache da capa em si continua 100% do lado do
IRIS (`GET /anime/capa/<chave>?url=...` já existia, ver Fase 2 acima) -
nenhuma mudança no MOIRAI além de expor os dados certos.

## Jikan API: inspiração de personagem popular pra Lista de Desejo da GAIA (2026-08-29)

Pedido do usuário: "ela pode se basear em animes tbm... ver os personagens
mais populares, comparar se eu ja vi o anime dele, e sugerir" - a Lista de
Desejo da GAIA (`assistant/features/lista_desejo/`) opcionalmente inspira
uma ideia de animação num personagem popular cujo anime o usuário já
assistiu. A API oficial do MAL (`mal_client.py`, `api.myanimelist.net/v2`)
não expõe NENHUM dado de personagem (só anime - busca/ranking/listas do
usuário) - confirmado testando na prática, não só por documentação.

- **`integrations/myanimelist/jikan_client.py` (novo)** - cliente da
  [Jikan API](https://jikan.moe) (`api.jikan.moe/v4`, não-oficial, sem
  autenticação, mantida pela comunidade a partir do site do MAL) -
  `obter_personagens_populares(paginas)` (`/top/characters`) e
  `obter_animes_do_personagem(character_id)` (`/characters/{id}/anime`,
  animeografia). Superfície de API separada do `mal_client.py` (OAuth2
  oficial) de propósito - são serviços diferentes.
- **`core/inspiracao_anime.py` (novo)** - orquestra: busca ~100 personagens
  populares + a animeografia de cada um, cacheia em disco (`data/
  jikan_personagens_cache.json`, TTL 7 dias - ranking de favoritos não muda
  de um dia pro outro) já que a Jikan tem limite público de ~3 req/s.
  Cruza os `mal_id` de anime da animeografia contra `obter_lista_watching`/
  `obter_lista_completed_com_notas` (API oficial, já existentes) pra achar
  um personagem cujo anime bate. Se o cache ainda não existe/expirou,
  devolve `None` NA HORA e reconstrói em thread separada (a reconstrução
  bate dezenas de vezes na Jikan, não cabe no tempo de uma chamada HTTP
  comum) - a próxima chamada já vem pronta.
- **`GET /mal/personagem_popular_assistido` (nova rota)** - `{"dados":
  {"personagem", "anime"} | null}`.
- **`mal_client.obter_lista_completed_com_notas` ganhou o campo `"id"`**
  (faltava - só tinha título/nota/gêneros) - precisava do `mal_id` real
  pra cruzar com a animeografia da Jikan por ID em vez de comparar título.

**⚠️ Achado ao vivo (2026-08-29)**: a Jikan está com uma queda geral desde
28/08 ([jikan-rest#612](https://github.com/jikan-me/jikan-rest/issues/612),
sem resposta dos mantenedores até agora) - `/top/anime` ainda responde
(cache deles), mas todo `/characters/*` devolve 504. O código já trata isso
do jeito certo (nunca lança exceção, só devolve `None` e a Lista de Desejo
segue sem essa inspiração) - não depende de nada aqui pra ser corrigido, só
da Jikan voltar. Sem teste automatizado (depende de rede externa de
terceiro, mesmo padrão dos outros clientes de API deste projeto).

## Assistente de Animes: renomeação de biblioteca silenciosa quando episódio recém-baixado não batia com o registro (2026-08-29/30)

Achado do usuário: "alguns animes recem baixados n foram renomeados, ate
cliquei no botao p renomear, mas tao reconhecendo". Causa raiz dupla,
ambas silenciosas até então:

1. `renomear_biblioteca_existente` já pulava de propósito um vídeo cujo
   número de episódio no arquivo é MAIOR que o "último episódio visto"
   registrado (proteção contra fansub que numera acumulado pela franquia
   inteira, ex.: Judas com Dr. Stone) - mas esse pulo nunca era reportado
   de volta, só descartado. Agora devolve `(resultados, pendentes)` -
   `pendentes` é `[(caminho, titulo_anime, numero_episodio,
   ultimo_conhecido), ...]`. `renomear_biblioteca_completa` e a rota `POST
   /anime/renomear_biblioteca` propagam os 2. Do lado da GAIA
   (`integrations/moirai_client.py`/`ui/qt_modais/animes.py`), isso vira
   um aviso explicando as 2 causas possíveis (episódio novo, registro
   ainda não atualizado - resolve sozinho; ou fansub numerando diferente -
   nunca resolve sozinho, precisa renomear na mão).
2. A etapa por hash (`renomear_por_hash_qbittorrent`) faz 1 requisição HTTP
   sequencial por anime "tenho interesse" (hoje 25) pra buscar a página do
   DarkMahou - medido em ~36s no total, contra os 30s do timeout padrão do
   cliente HTTP da GAIA. Um timeout genuíno virava silenciosamente "nada
   pra renomear, sem pendência" (mesmo sintoma do usuário). Fix do lado da
   GAIA: `_post` ganhou `timeout` configurável, essa chamada específica
   passou a usar 180s, e erro de rede real agora levanta `RuntimeError` em
   vez de devolver listas vazias.

## Categoria do IRIS renomeada pra "Watchlist" (2026-08-30)

Pedido do usuário: "No iris,gaia e moirai, renomeia Anime Tracker para
Watchlist". A MUDANÇA em si é só do lado do IRIS (`AnimeTrackerProvider.
rotulo_categoria`, ver `ARQUITETURA.md` do Project-IRIS) - o MOIRAI não tem
UI própria, só os comentários em `moirai/api_bridge.py` que citavam o nome
da categoria pelo texto foram atualizados pra continuar corretos
("categoria 'Anime Tracker' do popup" → "categoria 'Watchlist' do popup").
Nenhuma rota/endpoint/módulo (`anime_tracker.py`, `obter_anime_tracker_ativo`
etc.) mudou de nome - só a string exibida no popup do IRIS.

## Dados migrados (2026-08-24, verificados por checksum antes de remover da GAIA)

`data/anime_tracker_animes.json` (estado de cada anime), `data/
anime_tracker_checagem_diaria.json` (bookkeeping de catch-up), `data/
mal_token.json` (token OAuth real do usuário) e `data/anime_tracker_capas/`
(89 capas em cache) - a GAIA não guarda mais cópia nenhuma desses arquivos.

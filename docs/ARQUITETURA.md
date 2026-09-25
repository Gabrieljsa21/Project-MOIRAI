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

## Checagem de lançamentos: página do anime + UTF-8 forçado + histórico (2026-09-24)

Relato do usuário: ficou 1 semana fora e vários episódios não baixaram.
Diagnóstico com os dados reais (página de cada anime vs.
`ultimo_episodio_visto`):

- **Janela rotativa da home.** `verificar_novos_lancamentos` só lia
  "Últimos Lançamentos" (~20 vagas). O fechamento de gap de
  `_episodios_a_baixar` só funciona se o número do último lançado avança -
  com o PC desligado, o episódio da semana entrava e saía da home sem
  nenhuma checagem ver, e 10 animes ficaram parados. Correção:
  `_atualizar_lancamentos_fora_da_home` consulta a página de cada
  "tenho_interesse" que não apareceu na home (1 request por anime; pula quem
  já tem `ultimo_episodio_visto >= mal_num_episodios`). Só avança o número,
  nunca regride.
- **Charset ausente.** Desde 2026-09-23 o site responde
  `Content-Type: text/html` sem `charset`; o `requests` assume ISO-8859-1,
  "Episódio" vira "EpisÃ³dio" e `_extrair_opcoes_download` nunca achava o
  bloco. `_obter_html_darkmahou` centraliza os 5 pontos de scraping e força
  `resp.encoding = "utf-8"`. O download de capa (bytes) não passa por ele.
- **Timeout da GAIA.** A checagem completa leva ~50s (home + páginas +
  qBittorrent + MAL/AniList); o cliente da GAIA desistia em 30s e recebia o
  dict vazio de fallback, perdendo a notificação de "começou a baixar".
  Corrigido do lado da GAIA (`moirai_client.executar_checagem_completa`,
  timeout de 600s).
- **Histórico.** `anime_tracker_checagem_diaria.json` continua sendo só o
  gate de intervalo da GAIA (última data). O registro de cada checagem vai
  em `data/anime_tracker_historico_checagens.json` (lista, mais antiga
  primeiro no arquivo, limitada a 1000), gravado num `finally` pra
  registrar também checagens que quebram no meio (`erro`). Exposto por
  `GET /historico_checagens[?limite=N]`, mais recente primeiro.

- **Registro de downloads e buracos no meio.** `_episodios_a_baixar`
  partia do MAIOR episódio conhecido - um intermediário que falhasse (sem
  magnet ainda) deixava de ser tentado assim que um posterior baixava. Agora
  `baixar_episodio` grava cada disparo em `episodios_download_disparado_em`
  (`"AAAA-MM-DD HH:MM (hash)"`), e a lista a baixar é todo número entre o
  MENOR conhecido e o último lançado ausente de `_episodios_ja_tratados` -
  união de `episodios`, `downloads_em_andamento`, do registro novo e de
  todos os dicts de auditoria `episodios_*_em`/`episodios_erro_renomear`.
  `episodios` sozinho não serve como registro porque
  `sincronizar_biblioteca_local` remove "baixado" quando o arquivo é
  apagado; sem a auditoria, episódio apagado pelo usuário seria baixado de
  novo. Começar do menor (e não do 1) preserva quem começou a acompanhar no
  meio da temporada. Antes de ativar, a lógica nova foi simulada nos dados
  reais: só 2 buracos genuínos (Katainaka no Ossan E04, Yani Neko E05),
  baixados na checagem seguinte. Como efeito colateral, corrige também o
  caso em que todos os "baixado" de um anime eram revertidos e a lista vazia
  disparava backfill desde o episódio 1.

## Robustez de download: travados, numeração divergente, alertas e checagem autônoma (2026-09-24)

Segunda leva do mesmo dia, pedida pelo usuário ("pode implementar tudo")
depois da revisão de pendências:

- **Download travado.** `downloads_em_andamento[N]` ganhou `progresso` e
  `progresso_em` (renovado a cada avanço real). Sem avanço por
  `anime_download_travado_horas`, ou com o hash ausente do qBittorrent, o
  item vai para `_tratar_downloads_travados`, que roda DEPOIS de
  `verificar_downloads_em_andamento` salvar (porque `baixar_episodio`
  carrega/salva o JSON sozinho): remove o torrent com `delete_files=False`,
  guarda o hash em `episodios_magnets_tentados[N]` e chama `baixar_episodio`,
  que exclui os hashes tentados. Sem alternativa, a falha vai para
  `episodios_falha_download`. Downloads disparados antes desta versão, sem
  `progresso_em`, começam a contar a partir da primeira verificação.
- **Numeração divergente.** A trava de 2026-09-07 comparava o número no
  nome do arquivo com o esperado, e bloqueava igual tanto `content_path`
  errado quanto fansub com numeração própria. `_arquivo_pertence_ao_torrent`
  consulta `torrents_files` do hash pedido: se o arquivo está no torrent, o
  conteúdo é o que o site associou ao episódio e a renomeação segue. Para
  reduzir o caso na origem, `_escolher_melhor_magnet` desempata opções da
  mesma qualidade pelo número declarado no `dn` do magnet (a qualidade
  continua acima do desempate).
- **Sem censura primeiro.** A versão sem censura fica na mesma linha da
  tabela (legendado) que as demais, então `_escolher_melhor_magnet` ordena
  por (sem censura, qualidade, número no `dn`, ordem da página).
  `_PADRAO_SEM_CENSURA` confere rótulo e `dn` juntos porque nenhum dos dois
  é confiável sozinho: no caso real de Haite Kudasai, Takamine-san, o E10
  só diz "Sem Censura" no rótulo, e os E06/E07 têm rótulo "1080p Censura"
  com `[UNCENSORED]` no nome. Fica acima da qualidade porque o rótulo das
  versões sem censura não diz "HEVC" mesmo quando o arquivo é x265. Blocos
  de lote entram como opção quando sem censura (item abaixo).
- **Lotes sem censura.** `_extrair_opcoes_download` devolve
  `(rotulo, magnet, lote)`: `lote` é `None` no bloco "Episódio N" e
  `(inicio, fim)` num bloco com intervalo (`_intervalo_do_lote`: "01~04",
  "[01-12]") que cobre o episódio, com o título do bloco no rótulo. Lote
  com censura fica de fora: baixar um lote só compensa pela versão sem
  censura. Na escolha, lote perde para avulso no mesmo nível e o menor
  ganha. Fluxo no qBittorrent:
  - `baixar_episodio` adiciona o lote com `stop_condition="MetadataReceived"`
    (nada baixa antes da seleção) e grava `lote` no `downloads_em_andamento`.
    Se outro episódio do mesmo anime já acompanha o hash, não adiciona de
    novo, só registra.
  - `_aplicar_selecao_lotes`, a cada volta de `verificar_downloads_em_andamento`,
    agrupa por hash, acha o arquivo de cada episódio pelo número no nome
    (`_arquivo_do_episodio_no_lote`), põe prioridade 0 no resto e retoma.
    Idempotente; `lote_iniciado` cobre o lote em que todo arquivo foi
    pedido (nenhuma prioridade muda, mas precisa retomar).
  - Na conclusão, o arquivo vem do próprio lote (não o maior vídeo da
    pasta), a renomeação usa `torrents_rename_file` (o torrent segue ativo
    pros outros episódios) e `torrents_delete` só roda quando
    `_hash_ainda_acompanhado` é falso. `_tratar_downloads_travados` usa a
    mesma guarda.
  - O caminho novo da renomeação não repete a subpasta do torrent
    (`na_raiz=True`), então o qBittorrent move o arquivo pra `save_path`,
    junto dos episódios avulsos. Validado no qBittorrent real. Depois do
    `torrents_delete`, `_remover_subpastas_vazias_do_lote` tira as pastas
    vazias com `os.rmdir`, que nunca apaga arquivo.
- **O que conta como número de episódio.** Só título de bloco
  "Episódio N" (`_PADRAO_BLOCO_NUMERADO`/`_numero_do_bloco`), usado por
  `_ultimo_episodio_da_pagina`, `_blocos_especiais` e
  `_extrair_hashes_por_episodio`. Antes, o primeiro número de qualquer
  título valia, e página só com pacote (bloco com o nome do anime) gerava
  contagem falsa ("100-nin no Kanojo" = 100 episódios). `_PALAVRA_EPISODIO`
  (`Epi.?[oó]dio`) tolera um caractere trocado no lugar do "s" ("Epi8ódio
  18", caso real). Como `ultimo_episodio_visto` só cresce, um valor inflado
  não se corrige sozinho: precisa de reparo nos dados.
- **Página só com pacote.** Quando nenhum bloco é "Episódio N"
  (`_opcoes_pacote_completo` devolve vazio em página normal), a lista de
  episódios só existe dentro do torrent. `_baixar_pacote_completo` (chamado
  por `_baixar_pendentes_do_registro`, só com `ultimo_episodio_visto` vazio
  e sem pacote em andamento) escolhe a opção com `_escolher_melhor_magnet`,
  adiciona com `_adicionar_torrent(..., parar_no_metadado=True)` e grava
  `pacote_completo` = `{hash, rotulo, aguardando_arquivos, desde, sem_censura}`.
  `verificar_downloads_em_andamento` roda `_expandir_pacotes_completos` antes
  de montar os pendentes: com metadado, cada vídeo numerado vira entrada de
  `downloads_em_andamento` com `lote` (menos os de `_episodios_ja_tratados`)
  e `episodios_download_disparado_em` "(hash, pacote)"; o resto é o fluxo de
  lote. Sem metadado por `anime_download_travado_horas`, ou sem vídeo
  numerado, o torrent sai, o hash vai para `pacote_completo_tentados` e a
  próxima checagem tenta outra opção.
- **Link .torrent.** Páginas antigas do DarkMahou linkam
  `nyaa.si/download/N.torrent` em vez de magnet. `_PADRAO_LINK_DOWNLOAD`
  aceita os dois; `_hash_da_opcao`/`_nome_da_opcao` abstraem o tipo (magnet:
  `btih`/`dn`; .torrent: SHA-1 do trecho cru de `info` e `info.name`, via
  `_bdecode`, sem dependência nova). `_obter_torrent` guarda o resultado por
  processo e a falha por `_SEGUNDOS_CACHE_FALHA_TORRENT` (1h): o nyaa.si
  remove torrents, e a mesma opção é consultada várias vezes por escolha.
  `baixar_episodio` descarta .torrent indisponível e manda os mesmos bytes
  como `torrent_files`, para o hash acompanhado ser o do arquivo enviado.
- **Episódio especial.** O bloco "Episódio Especial" não tem número, e
  todo o fluxo (e a GAIA, em `moirai_client.obter_ultimos_episodios_por_status`)
  assume `episodios` com chave numérica. Por isso o especial usa o
  identificador `especial-K` (K = ordem entre os especiais da página) em
  `downloads_em_andamento`, `episodios_download_disparado_em` e demais dicts
  de auditoria (`_episodios_ja_tratados` já ignora chave não numérica), e o
  estado em `registro["especiais"][K]` = `{"apos": N, "status": ...}`.
  `_status_episodio`/`_definir_status_episodio` escolhem o lugar certo;
  `_rotulo_episodio` dá "6.5 - Especial 1" para log e notificação.
  `_blocos_especiais` calcula `apos` pelo maior episódio numerado antes do
  bloco (a página nem sempre está em ordem). `_especiais_a_baixar` consulta
  a página em toda checagem de anime não completo e uma vez
  (`especiais_verificado_em`) para anime completo; o cache de 2 minutos de
  `_obter_html_darkmahou` evita baixar a página de novo na mesma checagem.
  Na conclusão, o nome sai `Título - S01E06.5 - Especial 1.ext` (pedido do
  usuário: posição + qual especial), sem conferência de número no nome do
  arquivo. `renomear_biblioteca_existente` pula esse padrão
  (`_PADRAO_NOME_ESPECIAL`) para não tratá-lo como o E06. Painel da GAIA e
  IRIS ainda não exibem especiais.
- **Sufixo e rebaixamento.** `sem_censura` no `downloads_em_andamento`
  acrescenta `SUFIXO_SEM_CENSURA` (" [Sem Censura]") ao nome final.
  `rebaixar_sem_censura=True` em `baixar_episodio` ignora o status
  "baixado"/"assistido" (só se a opção escolhida for sem censura) e marca o
  download com a flag, para a guarda de "acompanhamento antigo" de
  `verificar_downloads_em_andamento` não descartá-lo; "assistido" não volta
  para "baixado". Não existe gatilho automático: foi usado à mão, uma vez.
- **Falhas e alertas.** `_registrar_falha_download` grava `desde`/`motivo`
  por episódio (sem magnet, todos os magnets já travaram, erro no
  qBittorrent); a entrada sai quando o download dispara. A checagem devolve
  `texto_alertas` juntando `_alertas_de_site` (home vazia; 2+ tentativas e
  nenhuma deu certo) e `_coletar_alertas_falha_persistente` (acima de
  `anime_alerta_falha_horas`, uma vez por episódio via `alertado`).
- **Checagem autônoma.** Exceção ao padrão "GAIA decide quando": com a
  GAIA fechada (conexão TCP recusada na porta do webhook), o loop de 5min
  roda a checagem se o histórico mostra mais de
  `anime_checagem_autonoma_intervalo_horas` desde a última. Não mexe em
  `anime_tracker_checagem_diaria.json` (o gate da GAIA), para a GAIA não
  pular a própria checagem ao voltar. Textos acionáveis ficam em
  `anime_tracker_resultados_nao_entregues.json` e são prefixados no
  resultado da próxima checagem com origem "gaia". `_lock_checagem` evita
  duas checagens simultâneas disparando o mesmo download.
- **Config nova** (`moirai/config.py`, editável no Painel da GAIA):
  `anime_download_travado_horas` (24), `anime_alerta_falha_horas` (48),
  `anime_checagem_autonoma_ativa` (true),
  `anime_checagem_autonoma_intervalo_horas` (6).
- **Logs.** `runtime_log.remover_logs_antigos` apaga logs diários com mais
  de 30 dias (só arquivos no padrão `AAAA-MM-DD.log`).

## Loop de downloads separado da manutenção (2026-09-24)

Pedido do usuário: os episódios terminam de baixar em poucos minutos e a
renomeação esperava até 5min pela próxima volta de `_loop_manutencao`.
`verificar_downloads_em_andamento` agora roda em `_loop_downloads`, thread
própria, a cada `anime_intervalo_downloads_segundos` (30s; `max(5, ...)`
contra valor absurdo). Polling em vez de evento: o qBittorrent tem "rodar
programa ao concluir", mas isso exigiria configurar o cliente à mão; 30s de
atraso máximo resolve o pedido sem dependência externa.

Com 2 threads + a checagem mexendo no mesmo JSON, entrou
`lock_estado_animes` (RLock, antes era um Lock só da checagem). As rotas
POST da ponte HTTP ficaram FORA de propósito: a checagem segura o lock por
~50s e o cliente da GAIA desiste em 30s, então marcar interesse durante uma
checagem viraria erro; a janela de colisão delas com o loop é de
milissegundos (cada função recarrega o JSON logo antes de salvar).

## Anime finalizado em pasta própria por temporada (2026-09-25)

Pedido do usuário: anime já finalizado que está sendo baixado fica cada um
na sua pasta, dentro de uma pasta da temporada em que foi baixado, na pasta
de assistidos (`E:\Downloads\Anime\2026 3-Verão\{Título}`). Numeração da
estação: 1-Inverno (jan-mar), 2-Primavera (abr-jun), 3-Verão (jul-set),
4-Outono (out-dez), para o Explorer ordenar cronologicamente.

- **O que é "finalizado".** `anime_ja_finalizado`: estreia 2 temporadas
  atrás ou antes, ou na temporada anterior com o site já no total de
  episódios do MAL. Anime da temporada atual, ou da anterior ainda
  lançando (dois cours seguidos, caso de Re:Zero 4), não entra. Sem
  `temporada_estreia`, também não.
- **Quando move.** `organizar_animes_finalizados` roda no loop de downloads
  (30s), depois de `verificar_downloads_em_andamento`, e move da RAIZ da
  pasta de downloads (episódio normal, sem censura e especial). Anime com
  download em andamento ou pacote aguardando metadado fica para depois:
  arquivo de lote continua no torrent até o último episódio. A movimentação
  acontece depois da renomeação, com o `save_path` do qBittorrent intacto,
  porque todo o fluxo de renomeação/lote assume a pasta de downloads. Nunca
  sobrescreve arquivo existente.
- **Temporada fixa por anime.** A primeira movimentação grava
  `registro["temporada_organizada"]`; os episódios seguintes vão para a
  mesma pasta mesmo que a estação vire no meio do download.
- **Baixado x assistido.** As pastas "AAAA N-Estação" ficam dentro da pasta
  de assistidos, mas `sincronizar_biblioteca_local` as conta como
  "baixado" (`_mapear_arquivos_por_titulo(..., ignorar=...)` as exclui da
  varredura de assistidos). Como o arquivo não sai da pasta do anime, o
  "assistido" vem do player do MOIRAI (`_concluir_episodio_assistido` marca
  o status sem mover) ou do Painel ("último assistido"); a varredura nunca
  rebaixa "assistido". `obter_primeiro_episodio_baixado` também procura
  nessas pastas.

## Dados migrados (2026-08-24, verificados por checksum antes de remover da GAIA)

`data/anime_tracker_animes.json` (estado de cada anime), `data/
anime_tracker_checagem_diaria.json` (bookkeeping de catch-up), `data/
mal_token.json` (token OAuth real do usuário) e `data/anime_tracker_capas/`
(89 capas em cache) - a GAIA não guarda mais cópia nenhuma desses arquivos.

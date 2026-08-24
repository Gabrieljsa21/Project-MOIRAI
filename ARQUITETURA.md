# Arquitetura do Project-MOIRAI

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
  (repo do IRIS) fala com `GET /anime/tenho_interesse`, `POST /anime/
  adicionar`, `POST /anime/assistir/<titulo>` - a categoria "🎬 Anime
  Tracker" do popup só aparece se o MOIRAI estiver respondendo (TCP connect
  simples, nunca cacheado).
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

## Dados migrados (2026-08-24, verificados por checksum antes de remover da GAIA)

`data/anime_tracker_animes.json` (estado de cada anime), `data/
anime_tracker_checagem_diaria.json` (bookkeeping de catch-up), `data/
mal_token.json` (token OAuth real do usuário) e `data/anime_tracker_capas/`
(89 capas em cache) - a GAIA não guarda mais cópia nenhuma desses arquivos.

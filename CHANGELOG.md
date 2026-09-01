# Changelog

Histórico de alto nível do que muda no MOIRAI, por versão. Ver
`ARQUITETURA.md` pro detalhe técnico completo.

## [Unreleased]

### Novidades
- **Jikan API: inspiração de personagem popular pra Lista de Desejo da GAIA (2026-08-29)** - `integrations/myanimelist/jikan_client.py` + `core/inspiracao_anime.py` (novos), rota `GET /mal/personagem_popular_assistido`. Ver "Jikan API" em `ARQUITETURA.md`.
- **`iniciar_moirai.bat`/`iniciar_moirai_oculto.vbs` (2026-09-01)** - roda o MOIRAI escondido via `pythonw.exe`, sem console. Usado pelo item "MOIRAI" da categoria "Projects" do IRIS (ver `Project-IRIS/ARQUITETURA.md`). Ver `README.md`.

### Alterado
- **Categoria do Menu Radial (IRIS) renomeada de "Anime Tracker" pra "Watchlist" (2026-08-30, pedido do usuário)** - mudança em código só do lado do IRIS; aqui só os comentários de `moirai/api_bridge.py` que citavam o nome foram atualizados. Ver "Categoria do IRIS renomeada..." em `ARQUITETURA.md`.

### Corrigido
- **Renomeação de biblioteca ficava silenciosa quando episódio recém-baixado não batia com o registro (2026-08-29/30, achado do usuário)** - trava de numeração acumulada (proteção contra fansub tipo Judas) agora reporta o que pulou em vez de só descartar; timeout de 30s do cliente HTTP da GAIA (curto demais pra etapa por hash, ~36s medido) também virava "nada pra renomear" em silêncio. Ver "Assistente de Animes: renomeação..." em `ARQUITETURA.md`.

## [0.1.0] - 2026-08-24 a 2026-08-25: Extração completa - Assistente de Animes (scraping, download, MAL/AniList) (PRs #1 a #7)

### Correções
- **README revisado (2026-08-24)** - corrigida a alegação de que a AniList
  sincroniza progresso (ela só valida o calendário oficial de lançamento,
  via `idMal`; quem sincroniza progresso é o MyAnimeList); corrigida a
  alegação de "nenhuma dependência externa" (DarkMahou, qBittorrent e
  `ffmpeg` são dependências externas de verdade, só não são a GAIA/IRIS);
  corrigido `GET /checagem_diaria` e a URL padrão do webhook, que
  apareciam quebrados por quebra de linha dentro do code span; documentados
  os formatos de vídeo aceitos e o critério real de "assistido" (mover o
  arquivo pra pasta configurada); origem do nome detalhada com as três
  Moiras (Cloto/Láquesis/Átropos).

### Novidades
- **Repositório criado (Fase 1 da extração pro Project MOIRAI, 2026-08-24)** -
  motor completo do Assistente de Animes (scraping do DarkMahou, estado,
  download automático via qBittorrent, sincronização com MyAnimeList/
  AniList) movido de `Project G.A.I.A/assistant/features/anime_tracker/
  anime_tracker.py`, rodando como processo próprio. Ponte HTTP (porta 8768)
  pro Project IRIS (`iris_plugin_moirai`) e pra GAIA (`integrations/
  moirai_client.py`). Guarda de instância única (porta 8769, mesmo padrão
  de Argus/IRIS). Dados reais migrados (estado de animes, token do MAL,
  capas), verificados por checksum antes de remover da GAIA.

- **Fase 2 concluída (2026-08-24)** - ponte HTTP (`moirai/api_bridge.py`)
  expandida com todos os endpoints que a UI rica da GAIA (`ui/qt_modais/
  animes.py`) precisa: marcar interesse, remover, baixar pendentes/
  selecionados, editar episódio manualmente, renomear biblioteca,
  sincronizar biblioteca sob demanda, casamento manual/automático com o
  MAL, e um endpoint de config genérico (`GET`/`POST /config`) que
  substitui os 14 pares de getter/setter que existiam em `brain_store.py`
  da GAIA antes da extração. Botão "🎬 Assistente de Animes" do Painel da
  GAIA restaurado - mesma UI de sempre, agora falando por HTTP. 2 mudanças
  de contrato reais (mudança de processo exigia): capa do anime vira bytes
  (`GET /anime/capa/<chave>`, não mais caminho de arquivo local) e assistir
  episódio (`POST /anime/assistir_chave/<chave>`) roda o player e monitora
  tudo dentro do próprio MOIRAI, já que é quem tem acesso ao disco de
  downloads.

- **`esta_completo(registro)` (2026-08-25, pedido do usuário)** - nova função
  em `anime_tracker.py`: True se o assistido localmente já bateu o total de
  episódios conhecido pelo MAL (`mal_num_episodios`, guardado no casamento -
  ver `casar_animes_com_mal`/`confirmar_casamento_mal`). Usada pela sub-aba
  "Completo" nova em "Acompanhando" (UI do lado da GAIA, `ui/qt_modais/
  animes.py`) e reaproveitada por `sincronizar_progresso_mal` (que já
  calculava a mesma conta pra decidir "completed" no MAL, agora sem
  duplicar a lógica).

- **Categoria "🎬 Anime Tracker" do IRIS lista só "Para assistir" (2026-08-24)** -
  novo endpoint `GET /anime/para_assistir` (`obter_titulos_para_assistir`)
  filtra pra só quem já tem episódio baixado pronto, junto com `chave`/
  `capa_url` de cada um - antes listava todo "tenho_interesse", inclusive
  sem nada baixado ainda, o que era um clique morto no popup (sem seletor de
  episódio lá). Cada anime também ganhou a própria capa como ícone no popup
  em vez de um emoji genérico (trabalho do lado do IRIS, ver `CHANGELOG.md`
  dele).

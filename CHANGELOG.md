# Changelog

Histórico de alto nível do que muda no MOIRAI, por versão. Ver
`ARQUITETURA.md` pro detalhe técnico completo.

## [Unreleased]

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
- **Repositório criado (Fase 1 da extração pro Project-MOIRAI, 2026-08-24)** -
  motor completo do Assistente de Animes (scraping do DarkMahou, estado,
  download automático via qBittorrent, sincronização com MyAnimeList/
  AniList) movido de `Project G.A.I.A/assistant/features/anime_tracker/
  anime_tracker.py`, rodando como processo próprio. Ponte HTTP (porta 8768)
  pro Project-IRIS (`iris_plugin_moirai`) e pra GAIA (`integrations/
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

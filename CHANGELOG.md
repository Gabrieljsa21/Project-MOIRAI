# Changelog

Histórico de alto nível do que muda no MOIRAI, por versão. Ver
`ARQUITETURA.md` pro detalhe técnico completo.

## [Unreleased]

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

### Pendente (Fase 2)
- UI própria (hoje só existe, desativada, dentro do Painel da GAIA).

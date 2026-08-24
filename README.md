# Project-MOIRAI

Gerenciador de animes/episódios - acompanha lançamentos novos em
[darkmahou.io](https://darkmahou.io/), baixa automaticamente (via magnet,
qBittorrent) os episódios dos animes marcados como acompanhados, organiza a
biblioteca local e sincroniza progresso com MyAnimeList/AniList. Roda 100%
sozinho (nenhuma dependência externa além do que está em `pyproject.toml`);
quem também usa a [GAIA](../Project%20G.A.I.A) (assistente pessoal do mesmo
autor) ou o [Project-IRIS](../Project-IRIS) ganha integração extra por HTTP
quando o MOIRAI estiver rodando.

Extraído da GAIA em 2026-08-24 - era a feature "Assistente de Animes",
morava em `features/anime_tracker/anime_tracker.py` (ver histórico completo
em `Project G.A.I.A/assistant/docs/FUNCIONALIDADES.md`/`CHANGELOG.md`).
Arquitetura completa e decisões de design em [`ARQUITETURA.md`](ARQUITETURA.md).

## A origem do nome

Moirai é o nome coletivo das Moiras da mitologia grega, responsáveis pelo
fio do destino de cada pessoa - aproximação proposital com "mirai", palavra
japonesa associada a "futuro". A ideia é um gerente pessoal do que ainda
falta assistir, não só um downloader.

## Uso standalone

```bash
uv venv
uv pip install -e .
python -m moirai.main
```

Sem janela nem bandeja (ainda) - roda em segundo plano, checando downloads
em andamento/biblioteca local/progresso do MyAnimeList a cada 5 minutos
(`moirai/main.py`), e expondo uma ponte HTTP na porta 8768
(`moirai/api_bridge.py`) pra quem quiser consultar/adicionar/marcar
interesse por fora (GAIA, IRIS, ou qualquer script).

A checagem de lançamentos novos (scraping da home do DarkMahou) NÃO tem loop
próprio de propósito - quem decide quando rodar é quem consulta `GET /
checagem_diaria` (a GAIA, pelo Agendador Diário, decide a hora e o que
anunciar; sem a GAIA rodando, chame o endpoint manualmente ou pelo IRIS).

Variáveis de ambiente opcionais (`.env`, ver `.env.example`):
- `MAL_CLIENT_ID` - habilita a sincronização com o MyAnimeList (OAuth2 PKCE,
  app "other" registrada em myanimelist.net/apiconfig).
- `QBITTORRENT_HOST`/`PORT`/`USUARIO`/`SENHA` - credenciais da Web UI do
  qBittorrent (padrão `localhost:8080`).
- `MOIRAI_GAIA_WEBHOOK_URL` - onde avisar quando um episódio é movido pra
  pasta de "assistidos" sozinho (padrão `http://127.0.0.1:8766/moirai/
  episodio_assistido`, a ponte HTTP da GAIA).

## Integração com o Project-IRIS

O plugin `iris_plugin_moirai` (repo do IRIS) adiciona a categoria "🎬 Anime
Tracker" ao popup - só aparece se o MOIRAI estiver rodando (checagem TCP
simples, `esta_disponivel()`, igual qualquer outro plugin do IRIS).

## Integração com a GAIA

`integrations/moirai_client.py` (repo da GAIA) fala com a ponte HTTP daqui -
usado pelo Agendador Diário (aviso proativo no Discord) e pelos comandos
explícitos `/adicionar_anime`, `/verificar_animes`, `/status_anime`,
`/progresso_anime`. Ver `moirai/api_bridge.py` pro contrato completo.

## Estado da extração (2026-08-24)

Completa - motor rodando sozinho (Fase 1) e a UI rica de gerenciamento no
Painel da GAIA (`ui/qt_modais/animes.py` - marcar interesse, editar
episódios manualmente, renomear biblioteca, casamento com MAL) reescrita
como cliente HTTP (Fase 2), com paridade funcional total pro que existia
antes da extração. Validado de ponta a ponta com dados reais.

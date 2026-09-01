<p align="center">
  <img src="moirai/assets/logo_moirai.png" alt="Moirai" width="180">
</p>

# Project MOIRAI

Gerenciador de animes/episódios - acompanha lançamentos novos em
[darkmahou.io](https://darkmahou.io/), valida o calendário oficial de
lançamento via AniList, baixa automaticamente (via magnet, qBittorrent) os
episódios dos animes marcados como acompanhados, organiza a biblioteca
local e sincroniza o progresso assistido com o MyAnimeList. O motor
principal (downloads, biblioteca, progresso) roda sozinho, sem depender da
GAIA nem do Project IRIS; a única exceção é a checagem diária de
lançamentos novos, que precisa ser disparada por fora (ver "Uso
standalone" abaixo). Depende de serviços externos de verdade além do
Python (DarkMahou, qBittorrent e, opcionalmente, MyAnimeList e `ffmpeg`);
quem também usa a [GAIA](../Project%20G.A.I.A) (assistente pessoal do mesmo
autor) ou o [Project IRIS](../Project-IRIS) ganha integração extra por HTTP
quando o MOIRAI estiver rodando.

Extraído da GAIA em 2026-08-24 - era a feature "Assistente de Animes",
morava em `features/anime_tracker/anime_tracker.py` (ver histórico completo
em `Project G.A.I.A/assistant/docs/FUNCIONALIDADES.md`/`CHANGELOG.md`).
Arquitetura completa e decisões de design em [`ARQUITETURA.md`](ARQUITETURA.md).

## A origem do nome

Moirai é o nome coletivo das três Moiras da mitologia grega:

- **Cloto** fia o fio da vida;
- **Láquesis** mede e conduz seu percurso;
- **Átropos** corta o fio, encerrando-o.

Esse ciclo é uma analogia direta com o que o projeto faz por cada anime -
descobrir uma obra nova, acompanhar seus episódios até o fim, e registrar
quando ela termina. Tem também uma aproximação proposital com "mirai",
palavra japonesa associada a "futuro" (referência ao próximo episódio e às
histórias que ainda faltam assistir) - a ideia é um gerente pessoal do que
ainda falta ver, não só um downloader.

## Uso standalone

```bash
uv venv
uv pip install -e .
python -m moirai.main
```

Sem janela nem bandeja - roda em segundo plano, checando downloads
em andamento/biblioteca local/progresso do MyAnimeList a cada 5 minutos
(`moirai/main.py`), e expondo uma ponte HTTP na porta 8768
(`moirai/api_bridge.py`) pra quem quiser consultar/adicionar/marcar
interesse por fora (GAIA, IRIS, ou qualquer script).

**Sem terminal aberto (2026-09-01)**: `iniciar_moirai_oculto.vbs` sobe o
processo escondido via `pythonw.exe`, sem janela de console nenhuma - mesmo
padrão do `iniciar_iris_oculto.vbs`/`iniciar_argus_oculto.vbs`. **Ainda sem
redirecionamento de log pra arquivo** (diferente da GAIA/ERIS, que já
espelham stdout/stderr - ver `_RedirecionadorLog` em `Project-ERIS/eris/
main.py`) - rodando assim, qualquer `print()`/traceback é descartado no
vazio; ver `TODO.md`.

A checagem de lançamentos novos (scraping da home do DarkMahou) NÃO tem loop
próprio de propósito - quem decide quando rodar é quem consulta
`GET /checagem_diaria` (a GAIA, pelo Agendador Diário, decide a hora e o que
anunciar; sem a GAIA rodando, chame o endpoint manualmente ou pelo IRIS).

A pasta de downloads e a pasta de "assistidos" são configuráveis
(`GET`/`POST /config`, ou pelo modal de Animes no Painel da GAIA). Um
episódio é considerado assistido quando o arquivo dele é movido (por você,
ou por outro player) da pasta de downloads pra pasta de assistidos -
"assistido" tem prioridade sobre "baixado" se o mesmo episódio aparecer,
por engano, nas duas. Formatos reconhecidos na biblioteca: `.mkv`, `.mp4`,
`.avi`; com `ffmpeg` no PATH (opcional), todo `.mp4` já renomeado no padrão
da biblioteca é convertido pra `.mkv` automaticamente (remux sem
recodificar, só pra manter um container único) - sem `ffmpeg` instalado,
essa conversão simplesmente não roda, o resto funciona normal.

Variáveis de ambiente opcionais (`.env`, ver `.env.example`):
- `MAL_CLIENT_ID` - habilita a sincronização com o MyAnimeList (OAuth2 PKCE,
  app "other" registrada em myanimelist.net/apiconfig).
- `QBITTORRENT_HOST`/`PORT`/`USUARIO`/`SENHA` - credenciais da Web UI do
  qBittorrent (padrão `localhost:8080`).
- `MOIRAI_GAIA_WEBHOOK_URL` - onde avisar quando um episódio é movido pra
  pasta de "assistidos" sozinho (padrão
  `http://127.0.0.1:8766/moirai/episodio_assistido`, a ponte HTTP da GAIA).

## Integração com o Project IRIS

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

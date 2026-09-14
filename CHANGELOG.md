# Changelog

Este arquivo registra as mudanças importantes do projeto. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e as versões seguem o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

Histórico de alto nível do que muda no MOIRAI, por versão. Ver
`docs/ARQUITETURA.md` pro detalhe técnico completo.

## [Unreleased]

### Adicionado

- **Endpoint `GET /pasta_downloads` para o IRIS** (2026-09-07): devolve o caminho configurado por `config.obter_anime_pasta_downloads`, permitindo que a categoria Watchlist restaure a ação de abrir a pasta local. Validado com servidor HTTP efêmero e configuração temporária.
- **Notificação de "começou a baixar" (2026-09-05, pedido do usuário na GAIA)** - `processar_downloads_pendentes` agora devolve também título+episódio de cada download disparado (não só o contador), e `formatar_texto_download_iniciado`/`executar_checagem_completa` (novo campo `texto_download_iniciado`) formatam isso pra notificação - antes só existia um contador que a GAIA imprimia no log, nunca mandava pro Discord. A checagem em si também passou a rodar por INTERVALO (não mais 1x/dia) do lado da GAIA - ver CHANGELOG de lá, "Assistente de Animes: checagem por intervalo...".
- **Jikan API: inspiração de personagem popular pra Lista de Desejo da GAIA (2026-08-29)** - `integrations/myanimelist/jikan_client.py` + `core/inspiracao_anime.py` (novos), rota `GET /mal/personagem_popular_assistido`. Ver "Jikan API" em `docs/ARQUITETURA.md`.
- **`iniciar_moirai.bat`/`iniciar_moirai_oculto.vbs` (2026-09-01)** - roda o MOIRAI escondido via `pythonw.exe`, sem console. Usado pelo item "MOIRAI" da categoria "Projects" do IRIS (ver `Project-IRIS/docs/ARQUITETURA.md`). Ver `README.md`.

### Alterado

- **Categoria do Menu Radial (IRIS) renomeada de "Anime Tracker" pra "Watchlist" (2026-08-30, pedido do usuário)** - mudança em código só do lado do IRIS; aqui só os comentários de `moirai/api_bridge.py` que citavam o nome foram atualizados. Ver "Categoria do IRIS renomeada..." em `docs/ARQUITETURA.md`.

### Corrigido

- **MOIRAI não iniciava pela GAIA e a lista parecia ter sido apagada (2026-09-07)**: o carregamento de `.env` introduzido no mesmo dia encerrava o processo antes do log quando a `.venv` existente ainda não tinha `python-dotenv`; a GAIA então convertia a indisponibilidade da API em uma lista vazia. A inicialização agora possui fallback compatível, e todos os arquivos persistentes apontam de forma absoluta para `Project-MOIRAI/data`, independentemente da pasta usada para iniciar o processo. O arquivo original com os estados dos animes não foi perdido nem substituído.
- **Renomeação aceitava o arquivo errado do qBittorrent sem avisar (2026-09-07)**: `content_path` do torrent podia apontar pra um episódio diferente do que estava sendo acompanhado (ex.: hash do E22 resolvendo pra um arquivo `S01E20`) - `verificar_downloads_em_andamento` agora extrai o número de episódio do PRÓPRIO nome do arquivo (`_numero_episodio_no_nome_arquivo`, padrão `SxxEyy` ou `- NN`) e, se divergir do episódio esperado, mantém em acompanhamento e registra o motivo em `episodios_erro_renomear` em vez de renomear/marcar como baixado errado. Também corrigido: o JSON de estado agora salva sempre que algo mudou de verdade (erro registrado, divergência detectada, etc.), não só quando um episódio termina com sucesso - antes esses casos intermediários podiam ficar sem persistir até o próximo ciclo.
- **`.env` local finalmente carregado pelo processo standalone** (2026-09-07): `moirai.main` usava `MAL_CLIENT_ID`, `QBITTORRENT_*` e outras configurações sem chamar `load_dotenv()`. O entrypoint agora carrega o arquivo da raiz com `override=True` antes de importar o restante do pacote; `python-dotenv` entrou nas dependências declaradas.
- **Logs preservados durante execução escondida** (2026-09-07): `pythonw.exe` descartava `print()` e tracebacks porque não havia console. `moirai.runtime_log` agora espelha `stdout` e `stderr` em `logs/AAAA-MM-DD.log`, com horário em cada linha e troca automática de arquivo na virada do dia. A pasta de runtime foi adicionada ao `.gitignore`. Validado em diretório temporário e por compilação do entrypoint.
- **Botão "▶️" (Assistente de Animes, GAIA) podia reabrir episódio já assistido (2026-09-05, achado do usuário)** - `obter_primeiro_episodio_baixado` escolhia o menor número de episódio achado por varredura CRUA da pasta de downloads, sem checar o status já rastreado - se uma cópia residual do arquivo continuasse fisicamente lá mesmo depois da cópia real já ter sido movida pra pasta de assistidos, o play reabria ela. Agora descarta qualquer candidato cujo `episodios[N]` já seja `"assistido"`.
- **"🔄 Verificar agora" (Assistente de Animes, GAIA) não listava os animes com download em andamento (2026-09-05, achado do usuário)** - `executar_checagem_completa` só devolvia quantos downloads foram DISPARADOS nessa checagem (`disparados`), nunca os que já estavam baixando de uma checagem anterior (torrent lento, por exemplo). Novo campo `texto_baixando_agora` (`formatar_texto_baixando_agora`) reaproveita `obter_animes_com_download_ativo` (já existia, sem nenhum uso desde a migração do Menu Radial pro IRIS) pra listar os títulos com download ativo agora.
- **Renomeação de biblioteca ficava silenciosa quando episódio recém-baixado não batia com o registro (2026-08-29/30, achado do usuário)** - trava de numeração acumulada (proteção contra fansub tipo Judas) agora reporta o que pulou em vez de só descartar; timeout de 30s do cliente HTTP da GAIA (curto demais pra etapa por hash, ~36s medido) também virava "nada pra renomear" em silêncio. Ver "Assistente de Animes: renomeação..." em `docs/ARQUITETURA.md`.

## [0.1.0] - 2026-08-25

### Corrigido
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

### Adicionado
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

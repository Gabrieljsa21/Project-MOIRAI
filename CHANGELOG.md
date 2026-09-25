# Changelog

Este arquivo registra as mudanças importantes do projeto. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e as versões seguem o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

Histórico de alto nível do que muda no MOIRAI, por versão. Ver
`docs/ARQUITETURA.md` pro detalhe técnico completo.

## [Unreleased]

### Adicionado

- **Anime finalizado em pasta própria por temporada (2026-09-25, pedido do usuário)**: episódio de anime que já terminou de passar sai da raiz de `E:\Downloads` para `E:\Downloads\Anime\{AAAA N-Estação}\{Título}` (ex.: `2026 3-Verão`; 1-Inverno, 2-Primavera, 3-Verão, 4-Outono), pela temporada em que foi baixado. `organizar_animes_finalizados` roda no loop de downloads, só para anime sem download em andamento. Essas pastas contam como "baixado", não "assistido"; assistido pelo player do MOIRAI, o episódio fica na pasta e só muda de status.
- **Página só com pacote da temporada (2026-09-24, pedido do usuário)**: anime cuja página não tem nenhum bloco "Episódio N", só um bloco com o nome do anime (caso real Kimi no Koto ga Daidaidaidaidaisuki na 100-nin no Kanojo, pacote DKB 1080p HEVC), passa a ser baixado. `_baixar_pacote_completo` adiciona o pacote parado no metadado (`registro["pacote_completo"]`); `_expandir_pacotes_completos` transforma cada vídeo com número no nome em episódio acompanhado como lote, só os ainda não tratados, e define `ultimo_episodio_visto`. Daí vale o fluxo de lote (seleção de arquivos, renomeação, raiz da pasta, remoção no último). Sem metadado em 24h, ou sem vídeo numerado, o pacote é descartado e outra opção é tentada. Link que não é magnet nem .torrent (Google Drive, sync.com) continua de fora: Hitsugi no Chaika: Avenging Battle só tem Google Drive.
- **Episódio especial (2026-09-24, pedido do usuário, caso real Kawaii dake ja Nai Shikimori-san)**: bloco "Episódio Especial" (sem número) passa a ser baixado. Vira `especial-K` (K = ordem na página) em `downloads_em_andamento` e nos registros de auditoria; o estado fica em `registro["especiais"][K]` = `{"apos": N, "status": ...}`, fora de `episodios` (a GAIA converte essas chaves com `int()`). Nome final pela posição, como pedido: `Título - S01E06.5 - Especial 1.mkv`; notificação/log mostram "Episódio 6.5 - Especial 1". A página é consultada a cada checagem enquanto o anime não está completo, e uma vez para anime já completo. O especial ainda não aparece no Painel da GAIA nem no IRIS.
- **Cache de 2 minutos no HTML do DarkMahou (2026-09-24)**: `_obter_html_darkmahou` baixava a mesma página uma vez por episódio (12 requests para um anime de 12 episódios); com a busca de especiais, seriam mais.
- **Lotes sem censura (2026-09-24, pedido do usuário)**: blocos de lote da página do anime ("Episódios 01~04 Sem Censura", "1ª Temporada Completa [01-12]") entram como opção do episódio que cobrem, só quando são sem censura. O torrent é adicionado com `stop_condition="MetadataReceived"` e `_aplicar_selecao_lotes` liga só os arquivos dos episódios pedidos antes de retomar. Vários episódios compartilham o mesmo torrent; a renomeação é feita pela API do qBittorrent (`torrents_rename_file`) e o torrent só sai da lista depois do último episódio. O arquivo vai para a raiz da pasta de downloads, junto dos outros episódios (pedido do usuário), e a subpasta do lote é removida se ficar vazia (validado ao vivo: E01-E04, E08 e E12 do Takamine-san terminaram na raiz, sem subpasta sobrando). Avulso ganha de lote no mesmo nível, e lote menor ganha de lote maior. Validado no qBittorrent real com Haite Kudasai, Takamine-san: o lote 01~04 baixa os 4 arquivos, a temporada completa só os E08 e E12.
- **Sufixo " [Sem Censura]" no nome renomeado (2026-09-24, pedido do usuário)**: episódio baixado de opção sem censura vira `Título - S01E05 [Sem Censura].mkv`. `_PADRAO_NOME_ARQUIVO` reconhece o sufixo.
- **`baixar_episodio(..., rebaixar_sem_censura=True)` (2026-09-24)**: baixa de novo um episódio já baixado/assistido, só se a melhor opção for sem censura; o arquivo antigo fica intocado e o status "assistido" não volta para "baixado". Usado uma vez, como exceção pedida pelo usuário, para Haite Kudasai, Takamine-san (E01-E10 e E12 disparados; E11 já era sem censura e só ganhou o sufixo).
- **Checagem autônoma com a GAIA fechada (2026-09-24, pedido do usuário)**: o loop de manutenção (`main._checagem_autonoma_se_preciso`) roda `executar_checagem_completa(origem="autonoma")` quando a porta da GAIA não responde e a última checagem registrada tem mais de `anime_checagem_autonoma_intervalo_horas` (6h). Downloads iniciados e alertas dessas checagens ficam em `data/anime_tracker_resultados_nao_entregues.json` e são entregues na próxima checagem da GAIA. Um lock impede checagens simultâneas.
- **Download travado troca de magnet (2026-09-24)**: `verificar_downloads_em_andamento` acompanha o progresso de cada torrent; sem avanço por `anime_download_travado_horas` (24h), ou com o torrent fora do qBittorrent, `_tratar_downloads_travados` remove o torrent (sem apagar arquivo) e dispara o próximo magnet, excluindo os já tentados (`episodios_magnets_tentados`).
- **Alertas na checagem (2026-09-24)**: novo campo `texto_alertas` - home vazia, todos os downloads da checagem falhando, e episódio em `episodios_falha_download` há mais de `anime_alerta_falha_horas` (48h, avisado uma vez só). Motivo de cada falha também vai para o histórico.
- **Desempate por número do episódio na escolha do magnet (2026-09-24)**: entre opções da mesma qualidade, `_escolher_melhor_magnet` prefere a que declara o número do episódio no nome. Caso real: a Judas numera Yomi no Tsugai 2 episódios atrás do site.
- **Rotação de logs (2026-09-24)**: `runtime_log.remover_logs_antigos` apaga `logs/AAAA-MM-DD.log` com mais de 30 dias.
- **Histórico de checagens de lançamentos (2026-09-24, pedido do usuário)**: toda execução de `executar_checagem_completa` grava início/fim, itens lidos na home, páginas de anime consultadas, episódios novos detectados (com a fonte, home ou página), downloads disparados e downloads que falharam em `data/anime_tracker_historico_checagens.json` (últimas 1000). Consulta via `GET /historico_checagens[?limite=N]`. Antes só existia a data da última checagem.
- **Endpoint `GET /pasta_downloads` para o IRIS** (2026-09-07): devolve o caminho configurado por `config.obter_anime_pasta_downloads`, permitindo que a categoria Watchlist restaure a ação de abrir a pasta local. Validado com servidor HTTP efêmero e configuração temporária.
- **Notificação de "começou a baixar" (2026-09-05, pedido do usuário na GAIA)** - `processar_downloads_pendentes` agora devolve também título+episódio de cada download disparado (não só o contador), e `formatar_texto_download_iniciado`/`executar_checagem_completa` (novo campo `texto_download_iniciado`) formatam isso pra notificação - antes só existia um contador que a GAIA imprimia no log, nunca mandava pro Discord. A checagem em si também passou a rodar por INTERVALO (não mais 1x/dia) do lado da GAIA - ver CHANGELOG de lá, "Assistente de Animes: checagem por intervalo...".
- **Jikan API: inspiração de personagem popular pra Lista de Desejo da GAIA (2026-08-29)** - `integrations/myanimelist/jikan_client.py` + `core/inspiracao_anime.py` (novos), rota `GET /mal/personagem_popular_assistido`. Ver "Jikan API" em `docs/ARQUITETURA.md`.
- **`iniciar_moirai.bat`/`iniciar_moirai_oculto.vbs` (2026-09-01)** - roda o MOIRAI escondido via `pythonw.exe`, sem console. Usado pelo item "MOIRAI" da categoria "Projects" do IRIS (ver `Project-IRIS/docs/ARQUITETURA.md`). Ver `README.md`.

### Alterado

- **Magnet sem censura tem prioridade (2026-09-24, pedido do usuário)**: `_escolher_melhor_magnet` escolhe primeiro a opção sem censura, detectada por "sem censura"/"uncensored" no rótulo OU no `dn` do magnet (o rótulo do site é inconsistente: há "1080p Censura" com `[UNCENSORED]` no nome). Só depois vêm qualidade (1080p HEVC > 1080p) e o desempate por número do episódio. Validado na página real de Haite Kudasai, Takamine-san: E05, E06, E07, E09, E10 e E11 passam a vir sem censura, os demais continuam 1080p HEVC.
- **Renomeação de episódio concluído em até 30s, não mais até 5min (2026-09-24, pedido do usuário)**: `verificar_downloads_em_andamento` saiu do loop de manutenção de 5min e ganhou loop próprio (`main._loop_downloads`), a cada `anime_intervalo_downloads_segundos` (30s, mínimo 5s, editável no Painel da GAIA). Sem download em andamento, cada volta só lê o JSON local. Os dois loops e a checagem completa compartilham `anime_tracker.lock_estado_animes` (RLock) para uma escrita no JSON não sobrescrever a outra.
- **Categoria do Menu Radial (IRIS) renomeada de "Anime Tracker" pra "Watchlist" (2026-08-30, pedido do usuário)** - mudança em código só do lado do IRIS; aqui só os comentários de `moirai/api_bridge.py` que citavam o nome foram atualizados. Ver "Categoria do IRIS renomeada..." em `docs/ARQUITETURA.md`.

### Corrigido

- **Número do nome do anime lido como episódio (2026-09-24, relato do usuário: "tá certo esse link ter 100 eps pra baixar?")**: `_ultimo_episodio_da_pagina` pegava o primeiro número do título de QUALQUER bloco da página. Em página só com pacote da temporada, o título do bloco é o nome do anime, e "Kimi no Koto ga Daidaidaidaidaisuki na **100**-nin no Kanojo" virou "último episódio = 100"; "**2**ª Temporada BD Completo" (Hitsugi no Chaika) virou 2. Agora só "Episódio N" conta (`_numero_do_bloco`, também usado por `_extrair_hashes_por_episodio` e pela posição de especial). Os dois registros foram reparados (último episódio volta a vazio; nenhum download tinha sido disparado). Essas páginas só têm o pacote da temporada, que o MOIRAI não baixa.
- **"Epi8ódio 18" nunca encontrado (2026-09-24, Re:Zero 4ª Temporada)**: erro de digitação do site no título do bloco fazia o E18 terminar em "nenhum magnet" a cada checagem desde 00:52. O padrão aceita um caractere qualquer no lugar do "s" (`_PALAVRA_EPISODIO`); "Episódios" (lote) continua de fora. E18 disparado na hora.
- **Anime com link .torrent nunca baixava (2026-09-24, caso real Kawaii dake ja Nai Shikimori-san)**: páginas mais antigas do DarkMahou linkam `https://nyaa.si/download/N.torrent` em vez de magnet, e os 12 episódios terminavam em "nenhum magnet na página". `_extrair_opcoes_download` aceita os dois tipos; o .torrent é baixado (`_obter_torrent`, cache por processo), o hash sai do SHA-1 do dicionário `info` (`_bdecode`, sem dependência nova) e vai pro qBittorrent como `torrent_files`. .torrent removido do nyaa.si (todo "1080p HEVC" desse anime dá 404) sai dos candidatos, e a escolha cai pro 1080p; a falha fica em cache por 1h.
- **Episódio com numeração absoluta travado para sempre (2026-09-24, Bleach E08 desde 23/09)**: a trava de divergência entre o número esperado e o do nome do arquivo não distinguia `content_path` errado (caso de 2026-09-07) de fansub com numeração própria ("S17E48" para o E08 da temporada). Agora o arquivo é aceito quando pertence ao próprio torrent pedido (`_arquivo_pertence_ao_torrent`, via `torrents_files`), com registro em `episodios_numeracao_divergente_aceita`; fora do torrent, a trava continua e o aviso sai uma vez por motivo, não mais a cada 5 minutos. Validado ao vivo: Bleach E08 e Yomi no Tsugai E23/E24 renomeados.
- **Campo legado `episodios_baixados` removido dos dados (2026-09-24)**: sempre vazio, em 19 registros, sem nenhum uso no código.
- **Episódio do meio perdido quando um posterior baixava (2026-09-24, pedido do usuário)**: `_episodios_a_baixar` partia do MAIOR episódio conhecido, então se o E17 falhasse (sem magnet) e o E18 baixasse, o E17 nunca mais era tentado. Todo download disparado agora fica registrado em `episodios_download_disparado_em` (data + hash, nunca apagado), e a lista a baixar passou a ser todo número entre o MENOR conhecido e o último lançado que não aparece em nenhum registro (`_episodios_ja_tratados`, que também considera os dicts de auditoria, para não baixar de novo episódio que o usuário apagou). Na primeira checagem real, fechou 2 buracos antigos (Katainaka no Ossan E04, Yani Neko E05).
- **Episódios não baixados depois de uma semana com o PC desligado (2026-09-24, relato do usuário)**: duas causas. (1) A detecção de episódio novo lia só "Últimos Lançamentos" da home, uma janela rotativa de ~20 vagas; episódios que entraram e saíram dela sem checagem nunca eram vistos (10 animes ficaram 1-2 episódios atrás). Agora cada anime "tenho_interesse" fora da home é conferido pela própria página (`_atualizar_lancamentos_fora_da_home`), exceto os que já lançaram todos os episódios segundo o MAL. (2) Desde 2026-09-23 o DarkMahou responde sem `charset`; o `requests` decodificava como ISO-8859-1, o regex `Epis[oó]dio` deixava de casar e todo download terminava em "Nenhum magnet encontrado" (títulos novos também gravados como "4Âª Temporada"). Todo scraping do site passa por `_obter_html_darkmahou`, que força UTF-8; os 4 títulos corrompidos foram reparados nos dados. Validado com teste offline (`testes/testar_checagem_fora_da_home.py`) e uma checagem real, que disparou os 20 episódios atrasados.
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

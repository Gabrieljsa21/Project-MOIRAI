# TODO - Project MOIRAI

## Fase 2 - UI própria

**Prioridade:** Alta | **Complexidade:** Alta | **Status:** 📋 Não iniciado

Reescrever `ui/qt_modais/animes.py` (repo da GAIA, ~1100 linhas, hoje
desativado no Painel) como interface própria do MOIRAI - janela/bandeja
próprias (mesmo padrão do IRIS, `iris/ui/settings_window.py`) ou cliente
HTTP fino que fala com a ponte já existente (`moirai/api_bridge.py`).
Precisa de ~20 endpoints novos além dos que já existem hoje (marcar
interesse, editar episódio manualmente, casamento manual com MAL/AniList,
renomear biblioteca, seletor de episódios pra baixar seletivamente - ver
lista completa de chamadas em `ARQUITETURA.md` da GAIA/histórico do PR de
extração).

## Registro no "🧩 Integrações" da GAIA

**Prioridade:** Média | **Complexidade:** Baixa | **Status:** 📋 Não iniciado

`atualizacao.INTEGRACOES_OPCIONAIS`/`PROJETOS_RASTREADOS` (repo da GAIA)
ainda não tem entrada pro MOIRAI (só Argus/IRIS) - falta `verificar_versao_
moirai()` e a entrada no dict, mesmo padrão do IRIS (`atualizar_repo_git_
local`, tipo "processo").

## Limpeza de código morto na GAIA

**Prioridade:** Baixa | **Complexidade:** Baixa | **Status:** 📋 Não iniciado

`brain_store.py` (repo da GAIA) ainda tem as 14 funções getter/setter que
`anime_tracker.py` usava antes da extração (agora substituídas por
`moirai/config.py`) - ficaram como código morto, não removidas na Fase 1
por segurança (arquivo grande, risco desproporcional pra um cleanup de
baixo impacto).

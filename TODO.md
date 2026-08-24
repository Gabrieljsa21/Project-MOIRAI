# TODO - Project MOIRAI

## Extração completa (Fases 1 e 2, 2026-08-24)

Motor + UI rica totalmente migrados da GAIA, validados de ponta a ponta
com dados reais. Ver `CHANGELOG.md`/`ARQUITETURA.md` pro detalhe completo.
Nada bloqueado no momento - próximos itens são melhorias, não pendências
da extração:

- **Pasta de downloads configurável no popup do IRIS** (`iris_plugin_
  moirai`, prioridade baixa) - o Menu Radial original tinha um item "📁
  Abrir pasta de downloads de animes" que o provider ainda não expõe;
  precisaria de mais um endpoint `GET /pasta_downloads`, ou aceitar que
  esse item específico não faz sentido fora do processo local da GAIA/
  MOIRAI.
- **Interface própria (janela/bandeja)** - hoje a única UI é a que já
  existia (`ui/qt_modais/animes.py`, no Painel da GAIA, agora como cliente
  HTTP) - o MOIRAI em si roda sem janela nenhuma. Uma interface própria
  (mesmo padrão do IRIS, `iris/ui/settings_window.py`) só faria sentido se
  um dia alguém quiser gerenciar animes sem a GAIA aberta - não é uma
  necessidade conhecida hoje, registrado só como ideia futura.

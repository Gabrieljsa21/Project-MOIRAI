# TODO - Project MOIRAI

## Extração completa (Fases 1 e 2, 2026-08-24)

Motor + UI rica totalmente migrados da GAIA, validados de ponta a ponta
com dados reais. Ver `CHANGELOG.md`/`ARQUITETURA.md` pro detalhe completo.
Nada bloqueado no momento - próximos itens são melhorias, não pendências
da extração:

- **Interface própria (janela/bandeja)** - hoje a única UI é a que já
  existia (`ui/qt_modais/animes.py`, no Painel da GAIA, agora como cliente
  HTTP) - o MOIRAI em si roda sem janela nenhuma. Uma interface própria
  (mesmo padrão do IRIS, `iris/ui/settings_window.py`) só faria sentido se
  um dia alguém quiser gerenciar animes sem a GAIA aberta - não é uma
  necessidade conhecida hoje, registrado só como ideia futura.

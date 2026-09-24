# TODO - Project MOIRAI

## Extração completa (Fases 1 e 2, 2026-08-24)

Motor + UI rica totalmente migrados da GAIA, validados de ponta a ponta
com dados reais. Ver `CHANGELOG.md`/`ARQUITETURA.md` pro detalhe completo.
Nada bloqueado no momento - próximos itens são melhorias, não pendências
da extração:

- **Arquivos parciais de torrent travado** (2026-09-24) - Prioridade
  Baixa, Complexidade Baixa, Status ⚠️ decisão pendente.
  `_tratar_downloads_travados` remove o torrent com `delete_files=False`
  (mesma regra do resto do projeto: nunca apagar arquivo), então o pedaço
  baixado do torrent morto fica na pasta de downloads com o nome original
  do fansub. Se isso começar a acumular, decidir entre apagar só parciais
  de torrent que o próprio MOIRAI disparou ou listar no Painel para
  limpeza manual.
- **Interface própria (janela/bandeja)** - hoje a única UI é a que já
  existia (`ui/qt_modais/animes.py`, no Painel da GAIA, agora como cliente
  HTTP) - o MOIRAI em si roda sem janela nenhuma. Uma interface própria
  (mesmo padrão do IRIS, `iris/ui/settings_window.py`) só faria sentido se
  um dia alguém quiser gerenciar animes sem a GAIA aberta - não é uma
  necessidade conhecida hoje, registrado só como ideia futura.

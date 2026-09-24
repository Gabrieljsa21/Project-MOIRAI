<p align="center">
  <img src="moirai/assets/logo_moirai.png" alt="Moirai" width="180">
</p>

# Project MOIRAI

Gerenciador que acompanha episódios de anime, cuida dos downloads e mantém o progresso da biblioteca.

## Recursos principais

- encontra lançamentos no DarkMahou;
- mantém animes acompanhados, de interesse e ignorados;
- confirma datas pelo AniList;
- identifica episódios novos e pendentes;
- envia downloads ao qBittorrent;
- organiza temporadas e renomeia os arquivos da biblioteca;
- registra o último episódio baixado e assistido;
- sincroniza o progresso com o MyAnimeList.

## Origem do nome

MOIRAI vem de *Moirai* (Μοῖραι), o nome grego das Moiras, as três divindades responsáveis pelo destino. Cloto fia o fio da vida e representa o início; Láquesis mede seu percurso; Átropos corta o fio e determina o fim.

Essa origem combina com o ciclo completo acompanhado pelo projeto: lançamento → descoberta → interesse → acompanhamento → download → organização → episódio assistido → progresso registrado. Em uma frase: **MOIRAI acompanha o fio de cada anime, do lançamento ao último episódio assistido.**

O nome *Moirai* ainda lembra foneticamente *mirai* (未来), que significa "futuro" em japonês. Essa é uma referência secundária apropriada ao tema de anime.

### Identidade visual

A moldura circular da logo usa três fios entrelaçados em branco, azul e dourado. Uma agulha ocupa o centro, ligando a imagem diretamente ao fio do destino das Moiras.

Ao lado da agulha, dois elementos formam uma figura parecida com o símbolo de reprodução. A mesma composição reúne **fio e agulha → destino** e **reprodução → anime**. O círculo fechado também representa o ciclo completo acompanhado pelo MOIRAI.

## Requisitos

- Python 3.11 ou mais recente;
- qBittorrent com a Web UI ativa;
- acesso ao DarkMahou e ao AniList;
- conta do MyAnimeList para a sincronização opcional;
- `ffmpeg` opcional para trocar o contêiner de `.mp4` para `.mkv`.

## Instalação e uso

```powershell
uv venv
uv pip install -e .
Copy-Item .env.example .env
python -m moirai.main
```

O serviço acompanha downloads e a biblioteca a cada cinco minutos. A consulta de novos lançamentos ocorre quando um cliente chama `GET /checagem_diaria`; com a GAIA fechada, o próprio serviço consulta a cada 6 horas (configurável) e guarda o resumo para a próxima consulta da GAIA. Cada consulta fica registrada em `data/anime_tracker_historico_checagens.json` e pode ser lida em `GET /historico_checagens`. A API local usa a porta `8768`.

Use `iniciar_moirai_oculto.vbs` para abrir sem terminal visível. O `.env.example` explica as opções do qBittorrent, MyAnimeList e webhook.

## Integrações com outros projetos

- **GAIA:** agenda a busca diária, apresenta avisos e oferece uma tela completa para gerenciar animes.
- **IRIS:** adiciona a categoria Watchlist ao menu radial para acesso rápido.

O motor de downloads, biblioteca e progresso continua ativo sem essas integrações.

## Documentação

- [Arquitetura](docs/ARQUITETURA.md)
- [Pendências](docs/TODO.md)
- [Histórico de versões](CHANGELOG.md)
- [Padrão de documentação](docs/PADRAO_DOCUMENTACAO.md)

## Situação atual

Downloads, biblioteca, progresso, AniList e MyAnimeList foram validados com dados reais.

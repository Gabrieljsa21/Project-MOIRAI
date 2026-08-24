"""Integração com a AniList (GraphQL, https://graphql.anilist.co) - Fase 1 do
Assistente de Animes (ver docs/TODO.md e features/anime_tracker/anime_tracker.py) -
calendário OFICIAL de lançamentos (próximo episódio + horário exato via
`nextAiringEpisode`), usado pra validar o que o DarkMahou publicou de fato (site
scrapeado, sem garantia nenhuma de estar em dia) e detectar temporada encerrada.

Diferente do MyAnimeList (integrations/myanimelist/mal_client.py, OAuth2, lê/escreve
a LISTA PESSOAL do usuário), a AniList aqui só busca dado PÚBLICO da obra (calendário
de lançamento é a mesma coisa pra qualquer pessoa que acompanhe aquele anime) - sem
login, token, ou Client ID nenhum, só uma chamada GraphQL por consulta.

Cruzamento com o anime rastreado no DarkMahou: sempre que possível, via `idMal`
(campo que a AniList expõe pra cruzar com o id do MyAnimeList) - exato, sem
ambiguidade nenhuma, desde que o anime já tenha sido casado com o MAL primeiro
(ver anime_tracker.casar_animes_com_mal - por isso a Fase 1 (AniList) aqui depende
da Fase 2 (MAL) já ter rodado pro anime em questão, mesmo as duas sendo
originalmente "independentes" na análise técnica - na prática, `idMal` é preciso
DEMAIS pra não aproveitar, evita reimplementar uma segunda busca fuzzy por título
só pra AniList)."""

import requests

URL_API = "https://graphql.anilist.co"
_TIMEOUT_REQUEST = 15

_CAMPOS_MEDIA = """
    id
    title { romaji english native }
    status
    episodes
    nextAiringEpisode { episode airingAt }
"""

_QUERY_POR_ID = f"""
query ($id: Int) {{
  Media(id: $id, type: ANIME) {{
    {_CAMPOS_MEDIA}
  }}
}}
"""

_QUERY_POR_MAL_ID = f"""
query ($idMal: Int) {{
  Media(idMal: $idMal, type: ANIME) {{
    {_CAMPOS_MEDIA}
  }}
}}
"""


def _chamar_graphql(query, variaveis):
    try:
        resp = requests.post(URL_API, json={"query": query, "variables": variaveis}, timeout=_TIMEOUT_REQUEST)
    except Exception as e:
        return None, f"Erro na API da AniList: {e}"
    dados = resp.json()
    if resp.status_code != 200 or "errors" in dados:
        return None, f"Erro na API da AniList: {dados.get('errors', resp.text)}"
    return dados.get("data"), None


def buscar_por_mal_id(mal_id):
    """Cruzamento exato via idMal (ver docstring do módulo - sempre preferir
    isso a busca por título, sem ambiguidade nenhuma). Devolve (media, erro) -
    media é None (sem erro) se a AniList simplesmente não tiver esse idMal
    catalogado (MAL e AniList não têm 100% de overlap)."""
    dados, erro = _chamar_graphql(_QUERY_POR_MAL_ID, {"idMal": mal_id})
    if erro:
        return None, erro
    return (dados or {}).get("Media"), None


def buscar_por_id(anilist_id):
    """Busca direta pelo id da própria AniList (já cruzado antes, ver
    anime_tracker.casar_animes_com_anilist) - mais barato que buscar por idMal
    de novo toda checagem."""
    dados, erro = _chamar_graphql(_QUERY_POR_ID, {"id": anilist_id})
    if erro:
        return None, erro
    return (dados or {}).get("Media"), None

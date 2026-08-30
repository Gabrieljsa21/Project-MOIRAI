# -*- coding: utf-8 -*-
"""Cliente da Jikan API (api.jikan.moe/v4, 2026-08-29) - API não-oficial (sem
autenticação, mantida pela comunidade a partir de dados do próprio site do
MAL) usada só pro que a API oficial (`mal_client.py`, `api.myanimelist.net/v2`)
não tem: dado de PERSONAGEM (ranking de popularidade, animeografia). Pedido
do usuário: "ela pode se basear em animes tbm... ver os personagens mais
populares, comparar se eu ja vi o anime dele" - ver
`moirai/core/inspiracao_anime.py` pro cruzamento com a lista real do usuário
(essa API não sabe nada sobre o usuário, só personagem/anime público).

🔥 limite público da Jikan é ~3 req/s / 60 req/min - por isso o chamador
(inspiracao_anime.py) cacheia agressivamente e nunca chama isso direto num
pedido ao vivo da GAIA."""
import requests

URL_API_BASE = "https://api.jikan.moe/v4"


def _chamar_jikan(caminho, params=None):
    try:
        resp = requests.get(f"{URL_API_BASE}{caminho}", params=params, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return None, f"Erro na Jikan API: {e}"
    return resp.json(), None


def obter_personagens_populares(paginas=4):
    """Personagens mais populares por favoritos - [{"mal_id", "name",
    "favorites"}, ...], ~25 por página. (dados, erro) - para na primeira
    página que falhar, mantendo o que já veio em vez de descartar tudo."""
    resultado = []
    for pagina in range(1, paginas + 1):
        dados, erro = _chamar_jikan("/top/characters", params={"page": pagina})
        if erro or not dados.get("data"):
            break
        resultado.extend(
            {"mal_id": item["mal_id"], "name": item["name"], "favorites": item.get("favorites", 0)}
            for item in dados["data"]
        )
    return (resultado, None) if resultado else (None, "Nenhum personagem popular veio da Jikan.")


def obter_animes_do_personagem(character_id):
    """Animes em que o personagem aparece - [{"mal_id", "title"}, ...]
    (animeografia completa, sem filtrar por papel - o cruzamento com o que o
    usuário assistiu não depende disso). (dados, erro)."""
    dados, erro = _chamar_jikan(f"/characters/{character_id}/anime")
    if erro or not dados:
        return None, erro
    itens = [{"mal_id": item["anime"]["mal_id"], "title": item["anime"]["title"]} for item in dados.get("data", [])]
    return itens, None

# -*- coding: utf-8 -*-
"""Inspiração em Anime pra Lista de Desejo da GAIA (2026-08-29, pedido do
usuário: "ela pode se basear em animes tbm... ver os personagens mais
populares, comparar se eu ja vi o anime dele, e sugerir"). A API oficial do
MAL (`mal_client.py`) não expõe personagem nenhum, só anime - esse
cruzamento usa a Jikan (`jikan_client.py`, não-oficial) só pra popularidade
de personagem + animeografia, casando por `mal_id` contra o que o usuário já
assistiu (Watching/Completed, API oficial).

Cache em disco (`data/jikan_personagens_cache.json`, TTL de 7 dias - ranking
de favoritos não muda de um dia pro outro) pra nunca bater na Jikan (limite
público ~3 req/s) num pedido ao vivo da GAIA. Se o cache expirou/nunca
existiu, devolve None NA HORA e reconstrói em thread separada (a
reconstrução bate dezenas de vezes na Jikan, levaria bem mais que o timeout
de uma chamada HTTP comum) - a próxima chamada já vem pronta."""
import json
import os
import random
import threading
import time

from moirai.integrations.myanimelist import jikan_client, mal_client

_ARQUIVO_CACHE = "data/jikan_personagens_cache.json"
_TTL_CACHE_SEGUNDOS = 7 * 24 * 60 * 60
_PAGINAS_PERSONAGENS_POPULARES = 4  # 25 por página -> ~100 personagens

_construindo_cache = False


def _carregar_cache():
    if not os.path.exists(_ARQUIVO_CACHE):
        return None
    try:
        with open(_ARQUIVO_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - cache.get("gerado_em", 0) > _TTL_CACHE_SEGUNDOS:
        return None
    return cache.get("personagens")


def _salvar_cache(personagens):
    os.makedirs(os.path.dirname(_ARQUIVO_CACHE), exist_ok=True)
    with open(_ARQUIVO_CACHE, "w", encoding="utf-8") as f:
        json.dump({"gerado_em": time.time(), "personagens": personagens}, f, ensure_ascii=False, indent=2)


def _construir_cache():
    global _construindo_cache
    try:
        personagens_base, erro = jikan_client.obter_personagens_populares(_PAGINAS_PERSONAGENS_POPULARES)
        if erro or not personagens_base:
            return
        resultado = []
        for personagem in personagens_base:
            animes, erro_animes = jikan_client.obter_animes_do_personagem(personagem["mal_id"])
            if erro_animes:
                continue
            resultado.append({**personagem, "animes": animes})
            time.sleep(0.4)  # 🔥 respeita o limite público da Jikan (~3 req/s)
        if resultado:
            _salvar_cache(resultado)
    finally:
        _construindo_cache = False


def obter_personagem_popular_assistido():
    """Sorteia UM personagem popular cujo anime o usuário já assistiu
    (Watching ou Completed) - {"personagem", "anime"} ou None (MAL não
    configurado, cache ainda não pronto, ou nenhuma interseção encontrada).
    Nunca levanta - é só um ingrediente opcional pro prompt da Lista de
    Desejo (GAIA), tudo bem seguir sem ele."""
    global _construindo_cache
    if not mal_client.esta_configurado():
        return None

    personagens = _carregar_cache()
    if personagens is None:
        if not _construindo_cache:
            _construindo_cache = True
            threading.Thread(target=_construir_cache, daemon=True).start()
        return None

    assistidos, _ = mal_client.obter_lista_watching()
    completos, _ = mal_client.obter_lista_completed_com_notas()
    ids_assistidos = {item["id"] for item in (assistidos or [])}
    ids_assistidos |= {item["id"] for item in (completos or []) if item.get("id")}
    if not ids_assistidos:
        return None

    candidatos = [
        {"personagem": personagem["name"], "anime": anime["title"]}
        for personagem in personagens
        for anime in personagem.get("animes", [])
        if anime["mal_id"] in ids_assistidos
    ]
    return random.choice(candidatos) if candidatos else None

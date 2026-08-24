# -*- coding: utf-8 -*-
"""Configuração persistida do MOIRAI (`data/moirai_config.json`) - equivalente local
das 14 funções que `features/anime_tracker/anime_tracker.py` lia de `brain_store.py`
na GAIA antes da extração (2026-08-24). Mesmo padrão de leitura (lê o arquivo inteiro
a cada chamada, sem cache em memória - o mesmo padrão usado em `brain_store.py`/
`iris/core/radial_menu.py`, importante pra quem edita a config por fora enquanto o
processo está de pé)."""
import json
import os

ARQUIVO_CONFIG = "data/moirai_config.json"


def _carregar():
    if os.path.exists(ARQUIVO_CONFIG):
        try:
            with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _salvar(dados):
    os.makedirs(os.path.dirname(ARQUIVO_CONFIG), exist_ok=True)
    with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=4, ensure_ascii=False)


def _definir_par(chave, valor):
    dados = _carregar()
    dados[chave] = valor
    _salvar(dados)


def obter_anime_pasta_downloads():
    return _carregar().get("anime_pasta_downloads", "E:\\Downloads")


def salvar_anime_pasta_downloads(caminho):
    _definir_par("anime_pasta_downloads", caminho)


def obter_anime_pasta_assistidos():
    return _carregar().get("anime_pasta_assistidos", "E:\\Downloads\\Anime")


def salvar_anime_pasta_assistidos(caminho):
    _definir_par("anime_pasta_assistidos", caminho)


def obter_mal_sync_ativo():
    return _carregar().get("mal_sync_ativo", False)


def salvar_mal_sync_ativo(estado):
    _definir_par("mal_sync_ativo", estado)


def obter_anime_lembrete_atraso_ativo():
    return _carregar().get("anime_lembrete_atraso_ativo", True)


def salvar_anime_lembrete_atraso_ativo(estado):
    _definir_par("anime_lembrete_atraso_ativo", estado)


def obter_anime_notificar_pendentes_ativo():
    return _carregar().get("anime_notificar_pendentes_ativo", True)


def salvar_anime_notificar_pendentes_ativo(estado):
    _definir_par("anime_notificar_pendentes_ativo", estado)


def obter_mal_confianca_minima():
    return _carregar().get("mal_confianca_minima", 82)


def salvar_mal_confianca_minima(percentual):
    _definir_par("mal_confianca_minima", percentual)


def obter_mal_margem_minima():
    return _carregar().get("mal_margem_minima", 12)


def salvar_mal_margem_minima(percentual):
    _definir_par("mal_margem_minima", percentual)


def obter_renomear_confianca_minima():
    return _carregar().get("renomear_confianca_minima", 80)


def salvar_renomear_confianca_minima(percentual):
    _definir_par("renomear_confianca_minima", percentual)


def obter_renomear_confianca_parcial():
    return _carregar().get("renomear_confianca_parcial", 85)


def salvar_renomear_confianca_parcial(percentual):
    _definir_par("renomear_confianca_parcial", percentual)


def obter_renomear_margem_parcial():
    return _carregar().get("renomear_margem_parcial", 10)


def salvar_renomear_margem_parcial(percentual):
    _definir_par("renomear_margem_parcial", percentual)


def obter_limiar_minutos_assistido():
    return _carregar().get("limiar_minutos_assistido", 15)


def salvar_limiar_minutos_assistido(minutos):
    _definir_par("limiar_minutos_assistido", minutos)


def obter_anilist_limite_atraso_horas():
    return _carregar().get("anilist_limite_atraso_horas", 18)


def salvar_anilist_limite_atraso_horas(horas):
    _definir_par("anilist_limite_atraso_horas", horas)


def obter_lembrete_limite_episodios():
    return _carregar().get("lembrete_limite_episodios", 3)


def salvar_lembrete_limite_episodios(numero):
    _definir_par("lembrete_limite_episodios", numero)


def obter_lembrete_limite_dias():
    return _carregar().get("lembrete_limite_dias", 7)


def salvar_lembrete_limite_dias(numero):
    _definir_par("lembrete_limite_dias", numero)


def obter_anime_tracker_ativo():
    """Liga/desliga o loop próprio do MOIRAI (downloads em andamento/biblioteca/
    MAL, a cada 5min, `moirai/main.py`) - independente do toggle da GAIA que
    decide SE/QUANDO ela consulta a checagem diária via HTTP."""
    return _carregar().get("anime_tracker_ativo", True)


def salvar_anime_tracker_ativo(estado):
    _definir_par("anime_tracker_ativo", estado)

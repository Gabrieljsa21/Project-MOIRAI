# -*- coding: utf-8 -*-
"""Integração com o Trakt (2026-10-18) - histórico de séries e filmes
assistidos, que o MyAnimeList não cobre (pedido do usuário: "ja q o MAL n tem
serie, tem outro site p gerenciar os episodios assistidos?").

Opt-in via `TRAKT_CLIENT_ID` e `TRAKT_CLIENT_SECRET` no `.env` da GAIA (o
MOIRAI herda o ambiente dela). Login pelo fluxo de dispositivo do Trakt: o
MOIRAI pede um código, o usuário digita em trakt.tv/activate e o MOIRAI
consulta a resposta até ela chegar - sem servidor de callback (diferente do
MAL). Webhook do Trakt é recurso VIP e não é usado: o MOIRAI é quem detecta
que o usuário assistiu e chama a API. Token (access + refresh) em
`data/trakt_token.json`, ignorado pelo git, renovado sozinho antes de vencer.

Só fala com o Trakt quando há algo pra marcar ou quando o usuário pede (tela
de configuração) - nada de consulta periódica sem motivo."""
import json
import os
import threading
import time
from datetime import datetime, timezone

import requests

from moirai.paths import caminho_dados

URL_API = "https://api.trakt.tv"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
CAMINHO_TOKEN = caminho_dados("trakt_token.json")
_TIMEOUT = 20
_RENOVAR_ANTES_SEGUNDOS = 24 * 3600

_lock_token = threading.Lock()
# Estado do login em andamento, lido pela tela (GET /midia/trakt/estado).
estado_conexao = {"status": "parado"}


def _client_id():
    return os.environ.get("TRAKT_CLIENT_ID", "")


def _client_secret():
    return os.environ.get("TRAKT_CLIENT_SECRET", "")


def esta_configurado():
    return bool(_client_id() and _client_secret())


def _cabecalhos(token=None):
    cabecalhos = {"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": _client_id()}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    return cabecalhos


# ---------- token ----------

def _carregar_token():
    try:
        with open(CAMINHO_TOKEN, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _salvar_token(token):
    os.makedirs(os.path.dirname(CAMINHO_TOKEN), exist_ok=True)
    temporario = CAMINHO_TOKEN + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=4)
    os.replace(temporario, CAMINHO_TOKEN)


def esta_conectado():
    return _carregar_token() is not None


def desconectar():
    with _lock_token:
        if os.path.exists(CAMINHO_TOKEN):
            os.remove(CAMINHO_TOKEN)
    estado_conexao.clear()
    estado_conexao["status"] = "parado"


def obter_token_valido():
    """Access token pronto pra uso, renovado se vence em menos de 1 dia.
    None se não houver login ou a renovação falhar."""
    with _lock_token:
        token = _carregar_token()
        if not token:
            return None
        vence_em = token.get("created_at", 0) + token.get("expires_in", 0)
        if time.time() < vence_em - _RENOVAR_ANTES_SEGUNDOS:
            return token["access_token"]
        try:
            resp = requests.post(f"{URL_API}/oauth/token", headers=_cabecalhos(), timeout=_TIMEOUT, json={
                "refresh_token": token["refresh_token"], "client_id": _client_id(),
                "client_secret": _client_secret(), "redirect_uri": REDIRECT_URI, "grant_type": "refresh_token",
            })
            resp.raise_for_status()
        except Exception as e:
            print(f" [SISTEMA] Trakt: não consegui renovar o login ({e}).")
            return None
        novo = resp.json()
        novo.setdefault("created_at", int(time.time()))
        _salvar_token(novo)
        return novo["access_token"]


# ---------- login (fluxo de dispositivo) ----------

def iniciar_conexao():
    """Pede o código ao Trakt e começa a esperar a autorização numa thread.
    Devolve o estado ({status, user_code, url, ...}) pra tela mostrar."""
    if not esta_configurado():
        return {"status": "erro", "erro": "Falta TRAKT_CLIENT_ID e/ou TRAKT_CLIENT_SECRET no .env da GAIA."}
    try:
        resp = requests.post(f"{URL_API}/oauth/device/code", headers=_cabecalhos(), timeout=_TIMEOUT,
                             json={"client_id": _client_id()})
        resp.raise_for_status()
    except Exception as e:
        return {"status": "erro", "erro": f"O Trakt não respondeu ({e})."}
    dados = resp.json()
    estado_conexao.clear()
    estado_conexao.update({
        "status": "aguardando", "user_code": dados["user_code"], "url": dados["verification_url"],
        "expira_em": time.time() + dados.get("expires_in", 600),
    })
    threading.Thread(target=_esperar_autorizacao, args=(dados["device_code"], dados.get("interval", 5)), daemon=True).start()
    return dict(estado_conexao)


def _esperar_autorizacao(device_code, intervalo):
    """Respostas do Trakt: 200 autorizou, 400 ainda esperando, 429 consultar
    mais devagar, 404/409/410/418 código inválido, usado, vencido ou negado."""
    fim = estado_conexao.get("expira_em", time.time() + 600)
    while time.time() < fim and estado_conexao.get("status") == "aguardando":
        time.sleep(intervalo)
        try:
            resp = requests.post(f"{URL_API}/oauth/device/token", headers=_cabecalhos(), timeout=_TIMEOUT, json={
                "code": device_code, "client_id": _client_id(), "client_secret": _client_secret(),
            })
        except Exception:
            continue
        if resp.status_code == 200:
            token = resp.json()
            token.setdefault("created_at", int(time.time()))
            with _lock_token:
                _salvar_token(token)
            estado_conexao.update({"status": "conectado", "usuario": obter_usuario()})
            print(" [SISTEMA] Trakt conectado.")
            return
        if resp.status_code == 429:
            intervalo += 1
        elif resp.status_code != 400:
            motivos = {404: "código inválido", 409: "código já usado", 410: "código vencido", 418: "autorização negada"}
            estado_conexao.update({"status": "erro", "erro": motivos.get(resp.status_code, f"HTTP {resp.status_code}")})
            return
    if estado_conexao.get("status") == "aguardando":
        estado_conexao.update({"status": "erro", "erro": "o código venceu antes da autorização"})


def obter_usuario():
    token = obter_token_valido()
    if not token:
        return None
    try:
        resp = requests.get(f"{URL_API}/users/settings", headers=_cabecalhos(token), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("user", {}).get("username")
    except Exception:
        return None


def obter_estado():
    """Pra tela de configuração."""
    estado = {"configurado": esta_configurado(), "conectado": esta_conectado(), "conexao": dict(estado_conexao)}
    estado["conexao"].pop("expira_em", None)
    return estado


# ---------- busca e histórico ----------

def buscar(tipo, titulo, ano=None):
    """Primeiro resultado da busca do Trakt: {"trakt", "titulo", "ano"} ou
    None. `tipo`: "show" ou "movie". A busca do Trakt casa títulos em
    português ("A Casa do Dragão" -> House of the Dragon, "Terra da Máfia" ->
    MobLand). Ano só ajuda em filme: em série, o ano do título do site é o da
    temporada, não o da estreia."""
    params = {"query": titulo, "limit": 1}
    if ano and tipo == "movie":
        params["years"] = ano
    try:
        resp = requests.get(f"{URL_API}/search/{tipo}", headers=_cabecalhos(), params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        resultados = resp.json()
    except Exception as e:
        print(f" [SISTEMA] Trakt: busca por \"{titulo}\" falhou ({e}).")
        return None
    if not resultados:
        return None
    item = resultados[0][tipo]
    return {"trakt": item["ids"]["trakt"], "titulo": item["title"], "ano": item.get("year")}


def _data_iso(texto_local):
    """"2026-10-18 14:30" (hora local) -> ISO UTC que o Trakt aceita."""
    try:
        local = datetime.strptime(texto_local, "%Y-%m-%d %H:%M").astimezone()
    except (TypeError, ValueError):
        local = datetime.now().astimezone()
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def marcar_assistidos(episodios=(), filmes=()):
    """`episodios`: [(trakt_id_da_serie, temporada, episodio, assistido_em)];
    `filmes`: [(trakt_id, assistido_em)]. Devolve (ok, erro)."""
    token = obter_token_valido()
    if not token:
        return False, "Trakt não conectado"
    series = {}
    for trakt_id, temporada, episodio, quando in episodios:
        temporadas = series.setdefault(trakt_id, {})
        temporadas.setdefault(temporada, []).append({"number": episodio, "watched_at": _data_iso(quando)})
    corpo = {
        "shows": [{"ids": {"trakt": tid}, "seasons": [{"number": s, "episodes": eps} for s, eps in temporadas.items()]}
                  for tid, temporadas in series.items()],
        "movies": [{"ids": {"trakt": tid}, "watched_at": _data_iso(quando)} for tid, quando in filmes],
    }
    try:
        resp = requests.post(f"{URL_API}/sync/history", headers=_cabecalhos(token), json=corpo, timeout=_TIMEOUT)
    except Exception as e:
        return False, f"o Trakt não respondeu ({e})"
    if resp.status_code in (200, 201):
        return True, None
    motivos = {401: "login do Trakt inválido - conecte de novo", 420: "limite da conta grátis do Trakt atingido",
               426: "recurso só para VIP no Trakt"}
    return False, motivos.get(resp.status_code, f"Trakt respondeu HTTP {resp.status_code}")

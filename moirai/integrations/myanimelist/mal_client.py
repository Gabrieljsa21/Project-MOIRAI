"""Integração com o MyAnimeList (API v2) - Fase 2 do Assistente de Animes (ver
docs/TODO.md e features/anime_tracker/anime_tracker.py) - sincroniza progresso de
episódio assistido automaticamente, marca "Completed" no último episódio, e lê a
lista "Watching" do usuário.

Opt-in via MAL_CLIENT_ID no .env (app "GAIA" registrada em
myanimelist.net/apiconfig, App Type "other" - sem Client Secret, fluxo OAuth2 com
PKCE). Diferente do Google Calendar (`integrations/google_calendar/google_calendar.py`,
usa `google_auth_oauthlib.InstalledAppFlow` pronto), o MAL não tem uma lib Python
oficial - o fluxo aqui é implementado à mão: PKCE (`code_verifier`/`code_challenge`,
MAL exige `code_challenge_method=plain` - challenge = o próprio verifier, diferente
do S256 mais comum em outros providers) + um servidor HTTP local temporário
(`http.server`) só pra capturar o `code` do redirect, já que não existe um app web
de verdade rodando. Token (access + refresh) salvo em `data/mal_token.json`,
ignorado pelo git - renovado sozinho quando expira (`obter_token_valido`)."""

import hashlib
import json
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import requests

CLIENT_ID = os.environ.get("MAL_CLIENT_ID")
CAMINHO_TOKEN = "data/mal_token.json"
PORTA_CALLBACK_LOCAL = 8934
REDIRECT_URI = f"http://localhost:{PORTA_CALLBACK_LOCAL}/mal_callback"

URL_AUTORIZACAO = "https://myanimelist.net/v1/oauth2/authorize"
URL_TOKEN = "https://myanimelist.net/v1/oauth2/token"
URL_API_BASE = "https://api.myanimelist.net/v2"


def esta_configurado():
    return bool(CLIENT_ID)


def _carregar_token():
    if not os.path.exists(CAMINHO_TOKEN):
        return None
    try:
        with open(CAMINHO_TOKEN, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _salvar_token(dados):
    os.makedirs(os.path.dirname(CAMINHO_TOKEN), exist_ok=True)
    with open(CAMINHO_TOKEN, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)


class _ServidorCallback(BaseHTTPRequestHandler):
    """Servidor HTTP local mínimo, só pra existir durante os ~1-2 minutos do fluxo
    de autorização - captura o `code` que o MAL manda de volta pro
    App Redirect URL cadastrado, depois se desliga sozinho."""
    codigo_recebido = None
    state_recebido = None

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        _ServidorCallback.codigo_recebido = query.get("code", [None])[0]
        _ServidorCallback.state_recebido = query.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<html><body><h2>Autorizado! Pode fechar esta aba e voltar pra Galateia.</h2></body></html>".encode("utf-8"))

    def log_message(self, format, *args):
        pass  # 🔥 silencia o log padrão do http.server (ruído no console, sem valor)


def iniciar_autorizacao():
    """Abre o navegador pro usuário logar/autorizar, sobe o servidor local
    temporário, espera o redirect chegar (bloqueia a thread chamadora até
    receber ou até 120s), troca o code pelo token, e salva. Devolve
    (sucesso: bool, mensagem: str) - pensado pra rodar numa thread separada da
    GUI (ver executar_em_thread, ui/qt_widgets.py) já que bloqueia esperando o
    usuário no navegador."""
    if not esta_configurado():
        return False, "MAL_CLIENT_ID não configurado no .env."

    code_verifier = secrets.token_urlsafe(96)[:128]
    state = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "code_challenge": code_verifier,  # 🔥 MAL exige method "plain" - challenge = o próprio verifier
        "code_challenge_method": "plain",
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    url_completa = f"{URL_AUTORIZACAO}?{urlencode(params)}"

    _ServidorCallback.codigo_recebido = None
    _ServidorCallback.state_recebido = None
    servidor = HTTPServer(("localhost", PORTA_CALLBACK_LOCAL), _ServidorCallback)
    servidor.timeout = 120
    thread_servidor = threading.Thread(target=servidor.handle_request, daemon=True)
    thread_servidor.start()

    webbrowser.open(url_completa)
    thread_servidor.join(timeout=120)

    codigo = _ServidorCallback.codigo_recebido
    if not codigo:
        return False, "Não recebi a autorização a tempo (120s) - tente de novo."
    if _ServidorCallback.state_recebido != state:
        return False, "State não bateu - possível interferência externa, tentativa abortada por segurança."

    try:
        resp = requests.post(URL_TOKEN, data={
            "client_id": CLIENT_ID,
            "code": codigo,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        }, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return False, f"Erro ao trocar o código pelo token: {e}"

    _salvar_token(resp.json())
    return True, "Autorizado com sucesso - token salvo."


def _renovar_token(token_atual):
    resp = requests.post(URL_TOKEN, data={
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": token_atual["refresh_token"],
    }, timeout=15)
    resp.raise_for_status()
    novo = resp.json()
    _salvar_token(novo)
    return novo


def obter_token_valido():
    """Devolve o access_token atual - MAL não manda expiry absoluto, então
    (mesmo padrão simples de outros providers) tenta usar o token guardado e só
    renova sob demanda (via _renovar_token) se uma chamada de API devolver 401 -
    ver _chamar_api. Devolve None se nunca foi autorizado ainda."""
    token = _carregar_token()
    return token["access_token"] if token else None


def _chamar_api(metodo, caminho, **kwargs):
    """Wrapper de request autenticado - se der 401 (token expirado), renova 1x
    e tenta de novo antes de desistir."""
    token = _carregar_token()
    if not token:
        return None, "Não autorizado ainda - conecte o MAL primeiro."
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token['access_token']}"
    resp = requests.request(metodo, f"{URL_API_BASE}{caminho}", headers=headers, timeout=15, **kwargs)
    if resp.status_code == 401:
        try:
            token = _renovar_token(token)
        except Exception as e:
            return None, f"Token expirado e não deu pra renovar: {e}"
        headers["Authorization"] = f"Bearer {token['access_token']}"
        resp = requests.request(metodo, f"{URL_API_BASE}{caminho}", headers=headers, timeout=15, **kwargs)
    try:
        resp.raise_for_status()
    except Exception as e:
        return None, f"Erro na API do MAL: {e}"
    return resp.json(), None


def obter_lista_watching():
    """Lista "Watching" do usuário - [{"id", "title", "num_episodes",
    "num_watched_episodes"}, ...]. (dados, erro) - erro None em caso de sucesso."""
    dados, erro = _chamar_api("GET", "/users/@me/animelist", params={
        "fields": "list_status,num_episodes",
        "status": "watching",
        "limit": 100,
    })
    if erro:
        return None, erro
    itens = [
        {
            "id": item["node"]["id"],
            "title": item["node"]["title"],
            "num_episodes": item["node"].get("num_episodes", 0),
            "num_watched_episodes": item["list_status"]["num_episodes_watched"],
        }
        for item in dados.get("data", [])
    ]
    return itens, None


_LIMITE_CARACTERES_BUSCA_MAL = 64  # 🔥 API do MAL devolve 400 Bad Request acima disso (testado 2026-08-02)


def obter_lista_completed_com_notas():
    """Lista "Completed" do usuário com nota - [{"title", "score", "genres"},
    ...], nota mais alta primeiro. Só inclui item com nota real (> 0) - sem
    nota não ajuda a recomendação baseada em gosto (feature 4, docs/TODO.md).
    (dados, erro) - erro None em caso de sucesso."""
    dados, erro = _chamar_api("GET", "/users/@me/animelist", params={
        "fields": "list_status,genres",
        "status": "completed",
        "limit": 100,
    })
    if erro:
        return None, erro
    itens = [
        {
            "title": item["node"]["title"],
            "score": item["list_status"]["score"],
            "genres": [g["name"] for g in item["node"].get("genres", [])],
        }
        for item in dados.get("data", [])
        if item["list_status"]["score"] > 0
    ]
    itens.sort(key=lambda i: i["score"], reverse=True)
    return itens, None


def buscar_anime(titulo):
    """Busca por título - [{"id", "title", "num_episodes"}, ...], só os campos
    úteis pra casar com o anime rastreado no DarkMahou (ver TODO.md - "risco real:
    título pode não bater exato, precisa de confirmação manual"). Trunca em
    `_LIMITE_CARACTERES_BUSCA_MAL` - o parâmetro `q` da API do MAL devolve 400
    Bad Request acima disso (títulos longos de isekai/light novel batem nesse
    limite com frequência), e mesmo truncado o resultado ainda cobre o anime
    certo (confirmado real: título de 89 caracteres cortado em 64 já trouxe o
    match exato como 1º resultado)."""
    dados, erro = _chamar_api("GET", "/anime", params={"q": titulo[:_LIMITE_CARACTERES_BUSCA_MAL], "limit": 10, "fields": "num_episodes"})
    if erro:
        return [], erro
    return [{"id": item["node"]["id"], "title": item["node"]["title"], "num_episodes": item["node"].get("num_episodes", 0)} for item in dados.get("data", [])], None


def obter_anime_por_id(mal_id):
    """Busca direta pelo id do MAL - usada quando o usuário cola o link/id
    manualmente no Painel (ver anime_tracker.confirmar_casamento_mal_manual,
    caso real: nenhum candidato da busca por título bateu certo). (dados:
    {"id", "title", "num_episodes"}|None, erro) - id inexistente no MAL
    devolve erro (404, capturado por _chamar_api)."""
    dados, erro = _chamar_api("GET", f"/anime/{mal_id}", params={"fields": "num_episodes"})
    if erro:
        return None, erro
    return {"id": dados["id"], "title": dados["title"], "num_episodes": dados.get("num_episodes") or None}, None


def atualizar_progresso(anime_id, num_episodios_assistidos, status=None):
    """Atualiza o progresso de um anime na lista do usuário - `status`
    ("watching"/"completed"/etc.) só é enviado se informado (None = mantém o
    status atual). Devolve (sucesso: bool, erro: str|None)."""
    dados_form = {"num_watched_episodes": num_episodios_assistidos}
    if status:
        dados_form["status"] = status
    _, erro = _chamar_api("PATCH", f"/anime/{anime_id}/my_list_status", data=dados_form)
    return erro is None, erro

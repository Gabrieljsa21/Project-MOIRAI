# -*- coding: utf-8 -*-
"""Ponte HTTP do MOIRAI (porta 8768) - mesmo padrão de `integrations/iris_bridge.py`
na GAIA (`BaseHTTPRequestHandler` simples, sem framework). 3 consumidores:

- **Project-IRIS** (`iris_plugin_moirai`, categoria "🎬 Anime Tracker" do popup) -
  `GET /anime/tenho_interesse`, `POST /anime/adicionar`, `POST /anime/assistir/
  <titulo>` - mesmo contrato que existia em `iris_bridge.py` antes da extração,
  de propósito, pra trocar só a URL do lado do IRIS.
- **GAIA, Agendador Diário (poll 1x/dia)** - `GET /checagem_diaria` (roda
  `executar_checagem_completa` e devolve os textos já formatados),
  `GET`/`POST /ultima_checagem_diaria` (bookkeeping de catch-up, mesmo padrão
  de sempre). A GAIA decide QUANDO chamar e o QUE DIZER no Discord - aqui só
  devolve o dado bruto.
- **GAIA, comandos explícitos "/status_anime"/"/progresso_anime"/
  "/adicionar_anime"/"/verificar_animes"** (`core/agent/comandos.py`) -
  `GET /anime/titulos_e_chaves`, `GET /anime/animes_rastreados`,
  `GET /anime/estados_lancamento_anilist`, `GET /anime/progresso/<chave>` -
  dado bruto, a formatação da resposta pro usuário continua na GAIA. Os 3
  endpoints `GET /mal/*` cobrem o mesmo papel pra quem chamava `mal_client`
  direto (recomendação baseada no MAL, `core/agent/turno.py`/`core/tools/
  handlers.py`) - a GAIA não guarda mais o token OAuth do MAL, só o MOIRAI.

Fase 1 da extração (2026-08-24) - NÃO inclui ainda os endpoints que a UI rica
(`ui/qt_modais/animes.py` na GAIA) usaria pra edição fina (marcar interesse,
renomear biblioteca, casamento manual com MAL, etc.) - isso é Fase 2
(reescrever a UI como cliente HTTP). Por enquanto, `POST /anime/adicionar`
cai direto no fallback "baixa tudo que já foi lançado" (sem o seletor de
episódios via Qt, que dependia da GUI thread da GAIA - não existe aqui)."""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from moirai.core import anime_tracker
from moirai.integrations.myanimelist import mal_client

LOCAL_API_HOST = "127.0.0.1"
LOCAL_API_PORT = 8768


def _ler_corpo_json(handler):
    tamanho = int(handler.headers.get("Content-Length", 0))
    try:
        return json.loads(handler.rfile.read(tamanho)) if tamanho else {}
    except Exception:
        return {}


class _API(BaseHTTPRequestHandler):
    def _responder_json(self, dados):
        corpo = json.dumps(dados).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path == "/anime/tenho_interesse":
            titulos = sorted(t for t, _ in anime_tracker.obter_titulos_tenho_interesse())
            self._responder_json(titulos)
        elif self.path == "/anime/titulos_e_chaves":
            # 🔥 Igual ao endpoint acima, mas com a chave junto - usado por
            # `core/agent/comandos.py` (GAIA) pra resolver "/status_anime
            # <termo>"/"/progresso_anime <termo>" contra o registro real.
            self._responder_json(list(anime_tracker.obter_titulos_tenho_interesse()))
        elif self.path == "/anime/animes_rastreados":
            self._responder_json(dict(anime_tracker.obter_animes_rastreados()))
        elif self.path == "/anime/estados_lancamento_anilist":
            self._responder_json([list(item) for item in anime_tracker.obter_estados_lancamento_anilist()])
        elif self.path.startswith("/anime/progresso/"):
            chave = urllib.parse.unquote(self.path[len("/anime/progresso/"):])
            registro = dict(anime_tracker.obter_animes_rastreados()).get(chave)
            if not registro:
                self.send_response(404)
                self.end_headers()
                return
            lancado, baixado, assistido = anime_tracker.obter_ultimos_episodios_por_status(registro)
            self._responder_json({"lancado": lancado, "baixado": baixado, "assistido": assistido})
        elif self.path == "/checagem_diaria":
            self._responder_json(anime_tracker.executar_checagem_completa())
        elif self.path == "/ultima_checagem_diaria":
            self._responder_json({"data": anime_tracker.obter_ultima_checagem_diaria()})
        elif self.path == "/mal/configurado":
            self._responder_json({"configurado": mal_client.esta_configurado()})
        elif self.path == "/mal/completos_com_notas":
            dados, erro = mal_client.obter_lista_completed_com_notas()
            self._responder_json({"dados": dados, "erro": erro})
        elif self.path == "/mal/watching":
            dados, erro = mal_client.obter_lista_watching()
            self._responder_json({"dados": dados, "erro": erro})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/anime/adicionar":
            # 🔥 Síncrono de propósito (2026-08-24) - `core/agent/comandos.py`
            # ("/adicionar_anime") precisa do resultado real (chave/erro/
            # quantos episódios foram disparados) na hora, pra responder no
            # mesmo turno de chat. O IRIS (que só dispara e esquece) já roda
            # essa chamada na própria thread de fundo dele, então não se
            # importa em esperar - ver `iris_plugin_moirai/providers.py`.
            url = _ler_corpo_json(self).get("url", "")
            chave, erro = anime_tracker.adicionar_anime_manual(url)
            disparados = anime_tracker.baixar_pendentes_de(chave) if not erro and chave else 0
            self._responder_json({"chave": chave, "erro": erro, "disparados": disparados})
        elif self.path.startswith("/anime/assistir/"):
            titulo = urllib.parse.unquote(self.path[len("/anime/assistir/"):])

            def _trabalho():
                chave = dict(anime_tracker.obter_titulos_tenho_interesse()).get(titulo)
                if not chave:
                    return
                numero, caminho = anime_tracker.obter_primeiro_episodio_baixado(chave)
                if not caminho:
                    return
                try:
                    anime_tracker.assistir_e_monitorar(chave, numero, caminho)
                except OSError:
                    pass
            threading.Thread(target=_trabalho, daemon=True).start()
            self.send_response(200)
            self.end_headers()
        elif self.path == "/ultima_checagem_diaria":
            data_str = _ler_corpo_json(self).get("data", "")
            anime_tracker.salvar_ultima_checagem_diaria(data_str)
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def iniciar_servidor_api():
    servidor = HTTPServer((LOCAL_API_HOST, LOCAL_API_PORT), _API)
    servidor.serve_forever()

# -*- coding: utf-8 -*-
"""Ponte HTTP do MOIRAI (porta 8768) - mesmo padrão de `integrations/iris_bridge.py`
na GAIA (`BaseHTTPRequestHandler` simples, sem framework). 3 consumidores:

- **Project-IRIS** (`iris_plugin_moirai`, categoria "🎬 Anime Tracker" do popup) -
  `GET /anime/para_assistir` (título+chave+capa_url, só quem já tem episódio
  baixado), `GET /anime/capa/<chave>?url=<capa_url>`, `POST /anime/adicionar`
  + `POST /anime/baixar_pendentes`, `POST /anime/assistir/<titulo>`.
- **GAIA, Agendador Diário (poll 1x/dia)** - `GET /checagem_diaria`,
  `GET`/`POST /ultima_checagem_diaria`. A GAIA decide QUANDO chamar e O QUE
  DIZER no Discord - aqui só devolve o dado bruto.
- **GAIA, `ui/qt_modais/animes.py` (Fase 2, 2026-08-24) + comandos explícitos
  + ferramentas de recomendação** - o resto dos endpoints abaixo, um por
  função que a UI/os comandos precisam (marcar interesse, remover, editar
  episódio manualmente, renomear biblioteca, casamento com MAL, config de
  pastas/limiares). `GET`/`POST /config` é genérico (uma chave por vez) -
  substitui os 16 pares de getter/setter que existiam em `brain_store.py`
  antes da extração (agora `moirai/config.py`).

`GET /anime/capa/<chave>?url=<capa_url>` devolve os BYTES da imagem (não um
caminho de arquivo) - a GAIA roda em processo/pasta diferente do MOIRAI,
então um caminho local (`data/anime_tracker_capas/...`) não faria sentido do
lado de lá; `QPixmap.loadFromData()` no cliente evita precisar de arquivo
local nenhum."""
import json
import mimetypes
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from moirai import config
from moirai.core import anime_tracker, inspiracao_anime
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

    def _responder_404(self):
        self.send_response(404)
        self.end_headers()

    def _responder_ok(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        caminho, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)

        if caminho == "/anime/tenho_interesse":
            titulos = sorted(t for t, _ in anime_tracker.obter_titulos_tenho_interesse())
            self._responder_json(titulos)
        elif caminho == "/anime/titulos_e_chaves":
            self._responder_json(list(anime_tracker.obter_titulos_tenho_interesse()))
        elif caminho == "/anime/para_assistir":
            # 🔥 2026-08-24, categoria "🎬 Anime Tracker" do Menu Radial (IRIS) -
            # só quem já tem episódio baixado pronto, com a capa junto (o "🎬
            # <título>" clicado ali abre o episódio direto, sem seletor).
            self._responder_json([
                {"titulo": titulo, "chave": chave, "capa_url": capa_url}
                for titulo, chave, capa_url in anime_tracker.obter_titulos_para_assistir()
            ])
        elif caminho == "/anime/animes_rastreados":
            self._responder_json(dict(anime_tracker.obter_animes_rastreados()))
        elif caminho == "/anime/estados_lancamento_anilist":
            self._responder_json([list(item) for item in anime_tracker.obter_estados_lancamento_anilist()])
        elif caminho == "/anime/temporada_atual":
            self._responder_json({"temporada": anime_tracker.obter_temporada_atual()})
        elif caminho.startswith("/anime/progresso/"):
            chave = urllib.parse.unquote(caminho[len("/anime/progresso/"):])
            registro = dict(anime_tracker.obter_animes_rastreados()).get(chave)
            if not registro:
                self._responder_404()
                return
            lancado, baixado, assistido = anime_tracker.obter_ultimos_episodios_por_status(registro)
            self._responder_json({"lancado": lancado, "baixado": baixado, "assistido": assistido})
        elif caminho.startswith("/anime/capa/"):
            chave = urllib.parse.unquote(caminho[len("/anime/capa/"):])
            capa_url = (params.get("url") or [""])[0]
            arquivo = anime_tracker.obter_capa_local(chave, capa_url)
            if not arquivo or not os.path.exists(arquivo):
                self._responder_404()
                return
            tipo, _ = mimetypes.guess_type(arquivo)
            with open(arquivo, "rb") as f:
                dados = f.read()
            self.send_response(200)
            self.send_header("Content-Type", tipo or "image/jpeg")
            self.end_headers()
            self.wfile.write(dados)
        elif caminho == "/checagem_diaria":
            self._responder_json(anime_tracker.executar_checagem_completa())
        elif caminho == "/ultima_checagem_diaria":
            self._responder_json({"data": anime_tracker.obter_ultima_checagem_diaria()})
        elif caminho == "/mal/configurado":
            self._responder_json({"configurado": mal_client.esta_configurado()})
        elif caminho == "/mal/completos_com_notas":
            dados, erro = mal_client.obter_lista_completed_com_notas()
            self._responder_json({"dados": dados, "erro": erro})
        elif caminho == "/mal/watching":
            dados, erro = mal_client.obter_lista_watching()
            self._responder_json({"dados": dados, "erro": erro})
        elif caminho == "/mal/personagem_popular_assistido":
            self._responder_json({"dados": inspiracao_anime.obter_personagem_popular_assistido()})
        elif caminho == "/config":
            self._responder_json(config._carregar())
        else:
            self._responder_404()

    def do_POST(self):
        caminho = self.path

        if caminho == "/anime/adicionar":
            url = _ler_corpo_json(self).get("url", "")
            chave, erro = anime_tracker.adicionar_anime_manual(url)
            self._responder_json({"chave": chave, "erro": erro})
        elif caminho == "/anime/baixar_pendentes":
            chave = _ler_corpo_json(self).get("chave", "")
            self._responder_json({"disparados": anime_tracker.baixar_pendentes_de(chave)})
        elif caminho == "/anime/baixar_episodios_selecionados":
            corpo = _ler_corpo_json(self)
            disparados = anime_tracker.baixar_episodios_selecionados(corpo.get("chave", ""), corpo.get("numeros", []))
            self._responder_json({"disparados": disparados})
        elif caminho == "/anime/marcar_interesse":
            corpo = _ler_corpo_json(self)
            anime_tracker.marcar_interesse(corpo.get("chave", ""), corpo.get("interesse", ""))
            self._responder_ok()
        elif caminho == "/anime/remover":
            anime_tracker.remover_anime_rastreado(_ler_corpo_json(self).get("chave", ""))
            self._responder_ok()
        elif caminho == "/anime/definir_ultimo_lancado":
            corpo = _ler_corpo_json(self)
            anime_tracker.definir_ultimo_lancado(corpo.get("chave", ""), corpo.get("numero", 0))
            self._responder_ok()
        elif caminho == "/anime/definir_ultimo_baixado":
            corpo = _ler_corpo_json(self)
            anime_tracker.definir_ultimo_baixado(corpo.get("chave", ""), corpo.get("numero", 0))
            self._responder_ok()
        elif caminho == "/anime/definir_ultimo_assistido":
            corpo = _ler_corpo_json(self)
            anime_tracker.definir_ultimo_assistido(corpo.get("chave", ""), corpo.get("numero", 0))
            self._responder_ok()
        elif caminho == "/anime/renomear_biblioteca":
            dry_run = bool(_ler_corpo_json(self).get("dry_run", True))
            resultados, pendentes = anime_tracker.renomear_biblioteca_completa(dry_run=dry_run)
            self._responder_json({
                "resultados": [list(item) for item in resultados],
                "pendentes_numeracao": [list(item) for item in pendentes],
            })
        elif caminho == "/anime/sincronizar_biblioteca":
            anime_tracker.sincronizar_biblioteca_local()
            self._responder_ok()
        elif caminho.startswith("/anime/assistir/"):
            titulo = urllib.parse.unquote(caminho[len("/anime/assistir/"):])

            def _trabalho_titulo():
                chave = dict(anime_tracker.obter_titulos_tenho_interesse()).get(titulo)
                if not chave:
                    return
                numero, caminho_arquivo = anime_tracker.obter_primeiro_episodio_baixado(chave)
                if not caminho_arquivo:
                    return
                try:
                    anime_tracker.assistir_e_monitorar(chave, numero, caminho_arquivo)
                except OSError:
                    pass
            threading.Thread(target=_trabalho_titulo, daemon=True).start()
            self._responder_ok()
        elif caminho.startswith("/anime/assistir_chave/"):
            # 🔥 Variante por CHAVE (2026-08-24, `ui/qt_modais/animes.py` já
            # tem a chave em mãos, evita ida-e-volta extra pra resolver
            # título -> chave que a variante acima (pro IRIS) precisa fazer.
            chave = urllib.parse.unquote(caminho[len("/anime/assistir_chave/"):])
            numero, caminho_arquivo = anime_tracker.obter_primeiro_episodio_baixado(chave)
            if not caminho_arquivo:
                self._responder_json({"sucesso": False, "erro": "Nenhum episódio baixado encontrado."})
                return
            try:
                conseguiu = anime_tracker.assistir_e_monitorar(chave, numero, caminho_arquivo)
            except OSError as e:
                self._responder_json({"sucesso": False, "erro": str(e)})
                return
            self._responder_json({"sucesso": True, "numero": numero, "monitorado": bool(conseguiu)})
        elif caminho == "/ultima_checagem_diaria":
            data_str = _ler_corpo_json(self).get("data", "")
            anime_tracker.salvar_ultima_checagem_diaria(data_str)
            self._responder_ok()
        elif caminho == "/mal/confirmar_casamento":
            corpo = _ler_corpo_json(self)
            anime_tracker.confirmar_casamento_mal(corpo.get("chave", ""), corpo.get("mal_anime_id"), corpo.get("mal_num_episodios"))
            self._responder_ok()
        elif caminho == "/mal/confirmar_casamento_manual":
            corpo = _ler_corpo_json(self)
            sucesso, mensagem = anime_tracker.confirmar_casamento_mal_manual(corpo.get("chave", ""), corpo.get("entrada", ""))
            self._responder_json({"sucesso": sucesso, "mensagem": mensagem})
        elif caminho == "/mal/ignorar_casamento":
            anime_tracker.ignorar_casamento_mal(_ler_corpo_json(self).get("chave", ""))
            self._responder_ok()
        elif caminho == "/mal/retentar_casamento":
            anime_tracker.retentar_casamento_mal(_ler_corpo_json(self).get("chave", ""))
            self._responder_ok()
        elif caminho == "/config":
            corpo = _ler_corpo_json(self)
            if "chave" in corpo:
                config._definir_par(corpo["chave"], corpo.get("valor"))
            self._responder_ok()
        else:
            self._responder_404()

    def log_message(self, format, *args):
        return


def iniciar_servidor_api():
    servidor = HTTPServer((LOCAL_API_HOST, LOCAL_API_PORT), _API)
    servidor.serve_forever()

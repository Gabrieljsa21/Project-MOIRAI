"""Teste isolado (sem rede): link do encurtador otakufilmes.org vira o magnet
depois das duas etapas de contagem, fica em cache em disco, e opção que não
resolve sai da lista."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

at.time.sleep = lambda s: None  # sem esperar a contagem de verdade

BASE = "https://otakufilmes.org/encurtar/redirect.php?id="
LINK_OK, LINK_FALHA = BASE + "aaa", BASE + "bbb"
MAGNET = "magnet:?xt=urn:btih:96b913663cd51de9d283f39ea54164d37734021f&dn=%5BErai-raws%5D%20Teste%20-%2004"

PAGINA = f"""<details class="dm-download-group"><summary><span class="dm-download-title">Episódio 04</span></summary>
<div class="dm-download-resolutions"><div class="dm-resolution"><span class="dm-quality">Baixar&gt;&gt;</span>
<div class="dm-hosts"><a data-dm-encurtado="true" href="{LINK_OK}"><span>1080p</span><span aria-hidden="true">↗</span></a>
<a data-dm-encurtado="true" href="{LINK_FALHA}"><span>1080p HEVC</span></a></div></div></div></details>"""


class _Resposta:
    def __init__(self, texto="", json_=None):
        self.text, self._json = texto, json_

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _SessaoFalsa:
    acessos = []

    def __init__(self):
        self.headers, self.posts = {}, 0

    def get(self, url, **kw):
        _SessaoFalsa.acessos.append(url)
        if "step=download" in url:
            return _Resposta('body:new URLSearchParams({flow:"abc123"})')
        return _Resposta("Etapa 1 de 2")

    def post(self, url, data=None, **kw):
        self.posts += 1
        assert "action=resolve" in url and data == {"flow": "abc123"}
        if self.posts == 1:
            return _Resposta(json_={"error": "countdown_pending", "retry_after": 3})
        if url.startswith(LINK_OK):
            return _Resposta(json_={"success": True, "url": MAGNET})
        return _Resposta(json_={"error": "invalid_session"})


def _preparar():
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_LINKS_ENCURTADOS = os.path.join(pasta, "links.json")
    at._falhas_encurtador.clear()
    at.requests.Session = _SessaoFalsa
    _SessaoFalsa.acessos = []
    at._obter_html_darkmahou = lambda url: at._normalizar_blocos_download(PAGINA)


def testar_encurtador_vira_magnet_e_falha_sai():
    _preparar()
    opcoes = at._extrair_opcoes_download("https://darkmahou.io/x/", 4)
    assert opcoes == [("1080p", MAGNET, None)], opcoes
    assert at._hash_da_opcao(opcoes[0][1]) == "96b913663cd51de9d283f39ea54164d37734021f"


def testar_magnet_resolvido_fica_em_cache():
    _preparar()
    at._extrair_opcoes_download("https://darkmahou.io/x/", 4)
    _SessaoFalsa.acessos = []
    assert at._extrair_opcoes_download("https://darkmahou.io/x/", 4) == [("1080p", MAGNET, None)]
    assert _SessaoFalsa.acessos == [], _SessaoFalsa.acessos  # nem o OK (disco) nem a falha (1h)
    assert at._extrair_hashes_por_episodio("https://darkmahou.io/x/") == {4: {"96b913663cd51de9d283f39ea54164d37734021f"}}


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("testar_") and callable(f):
            f()
            print("OK", nome)

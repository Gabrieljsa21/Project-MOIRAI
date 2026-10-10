"""Teste isolado (sem rede externa): a ponte HTTP atende a tela de animes da
GAIA enquanto outra rota demora (caso real 2026-10-10, checagem de ~60 s
deixava a lista vazia) e a leitura do JSON nunca pega uma gravação pela
metade."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai import api_bridge
from moirai.core import anime_tracker as at


def _porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def testar_rota_lenta_nao_segura_as_outras():
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    at._salvar_animes({"x": {"titulo": "X"}})
    at.obter_estados_lancamento_anilist = lambda: time.sleep(3) or []
    api_bridge.LOCAL_API_PORT = _porta_livre()
    threading.Thread(target=api_bridge.iniciar_servidor_api, daemon=True).start()
    base = f"http://127.0.0.1:{api_bridge.LOCAL_API_PORT}"
    time.sleep(0.3)

    lenta = threading.Thread(target=lambda: urllib.request.urlopen(base + "/anime/estados_lancamento_anilist", timeout=10).read())
    lenta.start()
    time.sleep(0.3)
    inicio = time.perf_counter()
    with urllib.request.urlopen(base + "/anime/animes_rastreados", timeout=10) as resp:
        dados = json.loads(resp.read())
    duracao = time.perf_counter() - inicio
    lenta.join()
    assert dados == {"x": {"titulo": "X"}}, dados
    assert duracao < 1, f"esperou a rota lenta: {duracao:.2f}s"


def testar_leitura_durante_gravacao_nunca_vem_vazia():
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    grande = {f"anime-{i}": {"titulo": f"Anime {i}", "episodios": {str(n): "baixado" for n in range(50)}} for i in range(200)}
    at._salvar_animes(grande)
    parar = threading.Event()

    def _gravar():
        while not parar.is_set():
            at._salvar_animes(grande)

    escritor = threading.Thread(target=_gravar)
    escritor.start()
    try:
        vazias = sum(1 for _ in range(200) if len(at._carregar_animes()) != len(grande))
    finally:
        parar.set()
        escritor.join()
    assert vazias == 0, f"{vazias} leituras pegaram o arquivo pela metade"


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("testar_") and callable(f):
            f()
            print("OK", nome)

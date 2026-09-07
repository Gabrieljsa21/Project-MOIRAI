"""Teste isolado da configuração exposta pela rota /pasta_downloads."""
import os
import sys
import json
import threading
import urllib.request
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai import config
from moirai.api_bridge import _API


def testar_pasta_padrao_e_configurada(tmp_path=None):
    import tempfile

    pasta = tempfile.mkdtemp()
    arquivo = os.path.join(pasta, "moirai_config.json")
    anterior = config.ARQUIVO_CONFIG
    config.ARQUIVO_CONFIG = arquivo
    try:
        assert config.obter_anime_pasta_downloads() == "E:\\Downloads"
        config.salvar_anime_pasta_downloads("D:\\Animes")
        assert config.obter_anime_pasta_downloads() == "D:\\Animes"
    finally:
        config.ARQUIVO_CONFIG = anterior


def testar_rota_expoe_a_pasta_configurada():
    import tempfile

    pasta = tempfile.mkdtemp()
    anterior = config.ARQUIVO_CONFIG
    config.ARQUIVO_CONFIG = os.path.join(pasta, "moirai_config.json")
    servidor = HTTPServer(("127.0.0.1", 0), _API)
    try:
        config.salvar_anime_pasta_downloads("D:\\Anime Downloads")
        thread = threading.Thread(target=servidor.handle_request)
        thread.start()
        with urllib.request.urlopen(f"http://127.0.0.1:{servidor.server_port}/pasta_downloads", timeout=2) as resposta:
            dados = json.loads(resposta.read())
        thread.join(timeout=2)
        assert dados == {"caminho": "D:\\Anime Downloads"}
    finally:
        servidor.server_close()
        config.ARQUIVO_CONFIG = anterior


if __name__ == "__main__":
    testar_pasta_padrao_e_configurada()
    testar_rota_expoe_a_pasta_configurada()
    print("PASS: configuração da pasta de downloads")

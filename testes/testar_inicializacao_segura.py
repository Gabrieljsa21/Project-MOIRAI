"""Regressões da inicialização e dos caminhos persistentes do MOIRAI."""
import os
import subprocess
import sys
from pathlib import Path


PASTA_PROJETO = Path(__file__).resolve().parent.parent


def testar_importa_sem_python_dotenv():
    codigo = """
import builtins
original = builtins.__import__
def sem_dotenv(nome, *args, **kwargs):
    if nome == 'dotenv':
        raise ModuleNotFoundError(nome)
    return original(nome, *args, **kwargs)
builtins.__import__ = sem_dotenv
import moirai.main
print('ok')
"""
    resultado = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=os.path.dirname(str(PASTA_PROJETO)),
        env={**os.environ, "PYTHONPATH": str(PASTA_PROJETO)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == "ok"


def testar_dados_sempre_apontam_para_o_projeto():
    from moirai import config
    from moirai.core import anime_tracker, inspiracao_anime
    from moirai.integrations.myanimelist import mal_client

    pasta_dados = PASTA_PROJETO / "data"
    caminhos = (
        config.ARQUIVO_CONFIG,
        anime_tracker.ARQUIVO_ANIMES,
        anime_tracker.ARQUIVO_CHECAGEM_DIARIA,
        anime_tracker.PASTA_CAPAS,
        inspiracao_anime._ARQUIVO_CACHE,
        mal_client.CAMINHO_TOKEN,
    )
    for caminho in caminhos:
        assert Path(caminho).is_absolute()
        assert Path(caminho).is_relative_to(pasta_dados)


if __name__ == "__main__":
    testar_importa_sem_python_dotenv()
    testar_dados_sempre_apontam_para_o_projeto()
    print("PASS: inicialização segura e caminhos persistentes absolutos")

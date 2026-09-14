"""Caminhos absolutos usados pelo MOIRAI.

O processo pode ser iniciado pela GAIA, por atalho ou pelo terminal. Nenhum dado
persistente deve depender do diretório de trabalho herdado pelo processo.
"""
from pathlib import Path


PASTA_PROJETO = Path(__file__).resolve().parent.parent
PASTA_DADOS = PASTA_PROJETO / "data"


def caminho_dados(*partes):
    return str(PASTA_DADOS.joinpath(*partes))

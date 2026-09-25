"""Teste isolado (sem rede, sem qBittorrent): episódio de anime finalizado sai
da raiz da pasta de downloads para "{assistidos}/{AAAA N-Estação}/{Título}",
continua contando como "baixado" e, assistido pelo player, fica na pasta."""
import json
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

_NOME_PASTA_TEMPORADA_REAL = at.nome_pasta_temporada


def _preparar(animes):
    base = tempfile.mkdtemp()
    downloads = os.path.join(base, "Downloads")
    assistidos = os.path.join(downloads, "Anime")
    os.makedirs(assistidos)
    at.ARQUIVO_ANIMES = os.path.join(base, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump(animes, f)
    at.obter_anime_pasta_downloads = lambda: downloads
    at.obter_anime_pasta_assistidos = lambda: assistidos
    at.obter_temporada_atual = lambda: "Verão 2026"
    at.nome_pasta_temporada = lambda data=None: "2026 3-Verão"
    return downloads, assistidos


def _criar(pasta, nome):
    with open(os.path.join(pasta, nome), "w") as f:
        f.write("x")


def testar_nome_pasta_temporada():
    assert _NOME_PASTA_TEMPORADA_REAL(datetime(2026, 1, 10)) == "2026 1-Inverno"
    assert _NOME_PASTA_TEMPORADA_REAL(datetime(2026, 5, 10)) == "2026 2-Primavera"
    assert _NOME_PASTA_TEMPORADA_REAL(datetime(2026, 9, 24)) == "2026 3-Verão"
    assert _NOME_PASTA_TEMPORADA_REAL(datetime(2026, 12, 1)) == "2026 4-Outono"


def testar_classificacao_de_finalizado():
    at.obter_temporada_atual = lambda: "Verão 2026"
    assert at.anime_ja_finalizado({"temporada_estreia": "Outono 2022"})
    assert at.anime_ja_finalizado({"temporada_estreia": "Inverno 2026"})
    assert not at.anime_ja_finalizado({"temporada_estreia": "Verão 2026", "mal_num_episodios": 12, "ultimo_episodio_visto": 12})
    assert at.anime_ja_finalizado({"temporada_estreia": "Primavera 2026", "mal_num_episodios": 12, "ultimo_episodio_visto": 12})
    assert not at.anime_ja_finalizado({"temporada_estreia": "Primavera 2026", "mal_num_episodios": 19, "ultimo_episodio_visto": 18})
    assert not at.anime_ja_finalizado({"temporada_estreia": "Primavera 2026"})
    assert not at.anime_ja_finalizado({"temporada_estreia": None})


def testar_move_so_finalizado_sem_download_ativo():
    downloads, assistidos = _preparar({
        "velho": {"titulo": "Anime Velho", "interesse": "tenho_interesse", "temporada_estreia": "Outono 2022",
                  "episodios": {}, "downloads_em_andamento": {}},
        "baixando": {"titulo": "Anime Baixando", "interesse": "tenho_interesse", "temporada_estreia": "Outono 2022",
                     "episodios": {}, "downloads_em_andamento": {"3": {"hash": "a" * 40}}},
        "atual": {"titulo": "Anime Atual", "interesse": "tenho_interesse", "temporada_estreia": "Verão 2026",
                  "episodios": {}, "downloads_em_andamento": {}},
    })
    for nome in ("Anime Velho - S01E01.mkv", "Anime Velho - S01E02 [Sem Censura].mkv",
                 "Anime Velho - S01E02.5 - Especial 1.mkv", "Anime Baixando - S01E01.mkv", "Anime Atual - S01E01.mkv"):
        _criar(downloads, nome)

    assert at.organizar_animes_finalizados() == 3
    pasta_anime = os.path.join(assistidos, "2026 3-Verão", "Anime Velho")
    assert sorted(os.listdir(pasta_anime)) == [
        "Anime Velho - S01E01.mkv", "Anime Velho - S01E02 [Sem Censura].mkv", "Anime Velho - S01E02.5 - Especial 1.mkv"]
    assert os.path.exists(os.path.join(downloads, "Anime Baixando - S01E01.mkv"))
    assert os.path.exists(os.path.join(downloads, "Anime Atual - S01E01.mkv"))
    assert at._carregar_animes()["velho"]["temporada_organizada"] == "2026 3-Verão"

    # Episódio seguinte vai para a mesma pasta, mesmo que a estação tenha mudado.
    at.nome_pasta_temporada = lambda data=None: "2026 4-Outono"
    _criar(downloads, "Anime Velho - S01E03.mkv")
    assert at.organizar_animes_finalizados() == 1
    assert os.path.exists(os.path.join(pasta_anime, "Anime Velho - S01E03.mkv"))

    # Destino já existe: nunca sobrescreve.
    _criar(downloads, "Anime Velho - S01E01.mkv")
    assert at.organizar_animes_finalizados() == 0
    assert os.path.exists(os.path.join(downloads, "Anime Velho - S01E01.mkv"))


def testar_pasta_do_anime_conta_como_baixado_e_assistido_fica_no_lugar():
    downloads, assistidos = _preparar({
        "velho": {"titulo": "Anime Velho", "interesse": "tenho_interesse", "temporada_estreia": "Outono 2022",
                  "episodios": {}, "downloads_em_andamento": {}},
    })
    pasta_anime = os.path.join(assistidos, "2026 3-Verão", "Anime Velho")
    os.makedirs(pasta_anime)
    _criar(pasta_anime, "Anime Velho - S01E01.mkv")
    _criar(pasta_anime, "Anime Velho - S01E02.mkv")
    _criar(assistidos, "Anime Velho - S01E03.mkv")  # raiz de assistidos continua valendo como assistido

    at.sincronizar_biblioteca_local()
    assert at._carregar_animes()["velho"]["episodios"] == {"1": "baixado", "2": "baixado", "3": "assistido"}

    numero, caminho = at.obter_primeiro_episodio_baixado("velho")
    assert numero == 1 and caminho == os.path.join(pasta_anime, "Anime Velho - S01E01.mkv")

    at.sincronizar_progresso_mal = lambda: None
    at._concluir_episodio_assistido("velho", 1, caminho)
    assert os.path.exists(caminho)
    assert at._carregar_animes()["velho"]["episodios"]["1"] == "assistido"
    at.sincronizar_biblioteca_local()
    assert at._carregar_animes()["velho"]["episodios"]["1"] == "assistido"
    assert at.obter_primeiro_episodio_baixado("velho")[0] == 2


if __name__ == "__main__":
    for nome, funcao in list(globals().items()):
        if nome.startswith("testar_"):
            funcao()
            print(f"OK {nome}")

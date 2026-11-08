"""Teste: só o último episódio de cada anime fica na raiz da pasta de
assistidos (2026-11-08, pedido do usuário: "a pasta E:\\Downloads\\Anime ta
ficando meio poluida, quero q fique apenas o ultimo ep de cd anime").
Usa uma pasta temporária; a Lixeira é trocada por uma lista."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at


def testar_fica_so_o_ultimo():
    pasta = tempfile.mkdtemp()
    try:
        nomes = ["Anime A - S01E01.mkv", "Anime A - S01E02 [Sem Censura].mkv", "Anime A - S01E03.mkv",
                 "Anime B - S01E05.mp4", "Anime B - S01E06.mp4",
                 "Anime C - S01E01.mkv", "Anime C - S01E02.mkv",     # E01 ainda não está "assistido"
                 "Sem Registro - S01E01.mkv", "Sem Registro - S01E02.mkv",
                 "anotacoes.txt"]
        for n in nomes:
            open(os.path.join(pasta, n), "wb").write(b"x")
        # Subpastas (temporada, _Pausados) ficam como estão.
        os.makedirs(os.path.join(pasta, "_Pausados"))
        open(os.path.join(pasta, "_Pausados", "Anime A - S01E01.mkv"), "wb").write(b"x")
        animes = {
            "a": {"titulo": "Anime A", "episodios": {"1": "assistido", "2": "assistido", "3": "assistido"}},
            "b": {"titulo": "Anime B", "episodios": {"5": "assistido", "6": "assistido"}},
            "c": {"titulo": "Anime C", "episodios": {"1": "baixado", "2": "assistido"}},
        }
        lixeira = []
        at.obter_anime_pasta_assistidos = lambda: pasta
        at._carregar_animes = lambda: animes
        at._mandar_para_lixeira = lambda c: (lixeira.append(os.path.basename(c)), os.remove(c))

        previstos = sorted(os.path.basename(c) for c in at.limpar_assistidos_antigos(dry_run=True))
        assert previstos == ["Anime A - S01E01.mkv", "Anime A - S01E02 [Sem Censura].mkv", "Anime B - S01E05.mp4"], previstos
        assert lixeira == []
        at.limpar_assistidos_antigos()
        assert sorted(lixeira) == previstos, lixeira
        assert sorted(os.listdir(pasta)) == ["Anime A - S01E03.mkv", "Anime B - S01E06.mp4", "Anime C - S01E01.mkv",
                                             "Anime C - S01E02.mkv", "Sem Registro - S01E01.mkv",
                                             "Sem Registro - S01E02.mkv", "_Pausados", "anotacoes.txt"]
        assert os.listdir(os.path.join(pasta, "_Pausados")) == ["Anime A - S01E01.mkv"]
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def testar_lixeira_real_do_windows():
    if os.name != "nt":
        return
    import importlib
    importlib.reload(at)
    pasta = tempfile.mkdtemp()
    try:
        caminho = os.path.join(pasta, "Anime - S01E01.mkv")
        open(caminho, "wb").write(b"x")
        at._mandar_para_lixeira(caminho)
        assert not os.path.exists(caminho)
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


if __name__ == "__main__":
    testar_fica_so_o_ultimo()
    testar_lixeira_real_do_windows()
    print("OK")

"""Teste isolado (sem qBittorrent real): episódio em 100% semeando com o
arquivo aberto pelo qBittorrent (WinError 32 no `os.rename`) é renomeado pela
API do qBittorrent e vira "baixado" (caso real 2026-10-07, Tensei Goblin E01)."""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

HASH = "a" * 40
_rename_real = os.rename


class _ClienteFalso:
    def __init__(self, torrent):
        self.torrent = torrent
        self.renomeados = []
        self.removidos = []

    def torrents_info(self, torrent_hashes):
        return [self.torrent]

    def torrents_rename_file(self, torrent_hash, old_path, new_path):
        self.renomeados.append((old_path, new_path))
        _rename_real(os.path.join(self.torrent.save_path, old_path), os.path.join(self.torrent.save_path, new_path))

    def torrents_delete(self, delete_files, torrent_hashes):
        assert delete_files is False
        self.removidos.append(torrent_hashes)


def _rename_bloqueado(origem, destino):
    raise PermissionError(32, "O arquivo já está sendo usado por outro processo")


def _preparar():
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump({"teste": {"titulo": "Tensei Goblin dakedo Shitsumon Aru", "url": "x", "interesse": "tenho_interesse",
                             "episodios": {}, "downloads_em_andamento": {"1": {"hash": HASH, "pasta": pasta}}}}, f)
    arquivo = os.path.join(pasta, "[Judas] TenGobu - S01E01.mkv")
    with open(arquivo, "wb") as f:
        f.write(b"x")
    at.qbittorrent_configurado = lambda: True
    at.obter_anime_pasta_downloads = lambda: pasta
    return pasta, arquivo


def testar_arquivo_em_uso_renomeia_pela_api():
    pasta, arquivo = _preparar()
    cliente = _ClienteFalso(SimpleNamespace(hash=HASH, progress=1.0, save_path=pasta, content_path=arquivo))
    at._cliente_qbittorrent = lambda: cliente
    at.os.rename = _rename_bloqueado
    try:
        at.verificar_downloads_em_andamento()
    finally:
        at.os.rename = _rename_real
    salvo = at._carregar_animes()["teste"]
    assert cliente.renomeados == [("[Judas] TenGobu - S01E01.mkv", "Tensei Goblin dakedo Shitsumon Aru - S01E01.mkv")], cliente.renomeados
    assert os.path.exists(os.path.join(pasta, "Tensei Goblin dakedo Shitsumon Aru - S01E01.mkv"))
    assert salvo["episodios"]["1"] == "baixado", salvo
    assert cliente.removidos == [HASH]


def testar_api_que_nao_renomeia_mantem_em_acompanhamento():
    pasta, arquivo = _preparar()
    cliente = _ClienteFalso(SimpleNamespace(hash=HASH, progress=1.0, save_path=pasta, content_path=arquivo))
    cliente.torrents_rename_file = lambda **kw: None  # API aceitou, mas o arquivo não mudou
    at._cliente_qbittorrent = lambda: cliente
    at._SEGUNDOS_ESPERA_RENAME_API = 1
    at.os.rename = _rename_bloqueado
    try:
        at.verificar_downloads_em_andamento()
    finally:
        at.os.rename = _rename_real
    salvo = at._carregar_animes()["teste"]
    assert "1" in salvo["downloads_em_andamento"] and "1" in salvo["episodios_erro_renomear"], salvo
    assert cliente.removidos == []


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("testar_") and callable(f):
            f()
            print("OK", nome)

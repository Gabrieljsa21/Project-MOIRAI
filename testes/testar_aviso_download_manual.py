"""Teste isolado (sem rede, sem qBittorrent real): anime adicionado por link
cuja página só tem "Episódio 00" (magnet) e um bloco "Temporada Completa"
com link do Yandex (caso real Boku no Kokoro no Yabai Yatsu 2ª Temporada,
2026-09-25). O episódio 0 vira especial (E00.5 - Especial 1). Com o zip do
Yandex cheio de .torrent, cada um vira o seu episódio; sem zip utilizável,
o aviso do download manual explica o bloco que ficou de fora."""
import io
import json
import os
import sys
import tempfile
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

at._buscar_nyaa = lambda consulta: []  # sem rede: reserva do nyaa desligada nos testes

URL = "https://darkmahou.io/anime-2a-temporada/"
URL_YANDEX = "https://disk.yandex.com/d/abc"
HASH = "7" * 40
NOME_ARQUIVO = "[Crocante] Anime 2nd Season - 00 (1080p) [x264][PT-BR].mkv"
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=%5BCrocante%5D%20Anime%202nd%20Season%20-%2000"
HTML = (
    '<h1>Anime 2ª Temporada</h1>'
    f'<div class="soraddl dlone"><h3>Episódio 00</h3><table><tr><td>Baixar</td>'
    f'<td><a href="{MAGNET}">1080p HEVC</a></td></tr></table></div>'
    '<div class="soraddl dlone"><h3>1ª Temporada Completa Legendado Torrent</h3><table><tr><td>Baixar</td>'
    f'<td><a href="{URL_YANDEX}">1080p</a><a href="https://jottacloud.com/s/y">1080p DDL Sync</a></td></tr>'
    '<tr><td>Senha</td><td><a href="#/">Darkanimes</a></td></tr></table></div>'
)


def _torrent_falso(nome):
    nome = nome.encode()
    return (b"d4:infod6:lengthi1e4:name" + str(len(nome)).encode() + b":" + nome
            + b"12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee")


def _zip_com_torrents(*numeros):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_:
        for n in numeros:
            nome = f"[Kirinashi] Anime S2 - {n:02d} [ABCD1234].mkv"
            zip_.writestr(f"[Kirinashi] Anime S2/{nome}.torrent", _torrent_falso(nome))
    return buffer.getvalue()


class _ClienteFalso:
    def __init__(self, pasta):
        self.pasta, self.adicionados, self.progress = pasta, [], 0.0

    def torrents_add(self, urls=None, torrent_files=None, **kw):
        self.adicionados.append(at._hash_do_magnet(urls) if urls else torrent_files)

    def torrents_info(self, torrent_hashes):
        return [SimpleNamespace(hash=torrent_hashes, progress=self.progress, save_path=self.pasta,
                                content_path=os.path.join(self.pasta, NOME_ARQUIVO))]

    def torrents_delete(self, delete_files, torrent_hashes):
        pass


def _preparar():
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump({}, f)
    at.qbittorrent_configurado = lambda: True
    at.obter_anime_pasta_downloads = lambda: pasta
    at._obter_html_darkmahou = lambda url: HTML
    at._cache_zips_yandex.clear()
    at._cache_torrents.clear()
    cliente = _ClienteFalso(pasta)
    at._cliente_qbittorrent = lambda: cliente
    return pasta, cliente


def _requests_falso(zip_bytes):
    def get(url, params=None, **kw):
        if url == at._API_YANDEX_PUBLICO:
            assert params == {"public_key": URL_YANDEX}
            dados = {"type": "file", "name": "Anime S2.zip", "size": len(zip_bytes), "file": "https://downloader/zip"}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: dados)
        assert url == "https://downloader/zip", url
        return SimpleNamespace(raise_for_status=lambda: None, content=zip_bytes)
    return get


def testar_episodio_zero_e_aviso_do_bloco_so_com_link_direto():
    pasta, cliente = _preparar()
    at._torrents_do_zip_yandex = lambda url: {}  # zip indisponível: bloco fica de fora

    chave, erro = at.adicionar_anime_manual(URL)
    assert erro is None
    assert "ultimo_episodio_visto" not in at._carregar_animes()[chave]  # "Episódio 00" não conta como numerado

    disparados, aviso = at.baixar_pendentes_com_aviso(chave)
    assert disparados == 1 and cliente.adicionados == [HASH]
    assert "1ª Temporada Completa Legendado Torrent" in aviso and "Nada novo" not in aviso

    disparados, aviso = at.baixar_pendentes_com_aviso(chave)
    assert disparados == 0
    assert aviso.startswith("Nada novo pra baixar") and "link de download direto" in aviso

    open(os.path.join(pasta, NOME_ARQUIVO), "w").close()
    cliente.progress = 1.0
    at.verificar_downloads_em_andamento()
    salvo = at._carregar_animes()[chave]
    assert salvo["especiais"]["1"] == {"apos": 0, "status": "baixado"}, salvo["especiais"]
    assert salvo.get("episodios") == {}
    assert os.path.isfile(os.path.join(pasta, "Anime 2ª Temporada - S02E00.5 - Especial 1.mkv")), os.listdir(pasta)


def testar_zip_do_yandex_vira_um_torrent_por_episodio():
    _, cliente = _preparar()
    at._torrents_do_zip_yandex = _TORRENTS_DO_ZIP_ORIGINAL
    at.requests.get, original = _requests_falso(_zip_com_torrents(1, 2)), at.requests.get
    try:
        chave, erro = at.adicionar_anime_manual(URL)
        assert erro is None and at._carregar_animes()[chave]["ultimo_episodio_visto"] == 2

        disparados, aviso = at.baixar_pendentes_com_aviso(chave)
        salvo = at._carregar_animes()[chave]
        assert disparados == 3, (disparados, aviso)  # E01, E02 do zip + o especial (Episódio 00)
        assert sorted(salvo["downloads_em_andamento"]) == ["1", "2", "especial-1"]
        assert aviso is None  # o bloco do Yandex foi aproveitado, nada ficou de fora
        assert [a for a in cliente.adicionados if isinstance(a, bytes)] == [
            _torrent_falso(f"[Kirinashi] Anime S2 - {n:02d} [ABCD1234].mkv") for n in (1, 2)]
    finally:
        at.requests.get = original


def testar_aviso_sem_qbittorrent():
    at.qbittorrent_configurado = lambda: False
    assert at.baixar_pendentes_com_aviso("qualquer") == (0, at._AVISO_SEM_QBITTORRENT)
    assert at.baixar_episodios_selecionados_com_aviso("qualquer", [1]) == (0, at._AVISO_SEM_QBITTORRENT)


_TORRENTS_DO_ZIP_ORIGINAL = at._torrents_do_zip_yandex

if __name__ == "__main__":
    testar_episodio_zero_e_aviso_do_bloco_so_com_link_direto()
    testar_zip_do_yandex_vira_um_torrent_por_episodio()
    testar_aviso_sem_qbittorrent()
    print("OK")

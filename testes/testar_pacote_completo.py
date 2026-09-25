"""Teste isolado (sem rede, sem qBittorrent real): página só com pacote da
temporada (bloco com o nome do anime, sem "Episódio N") - o pacote é
adicionado parado, a lista de arquivos vira os episódios, só os não
tratados são baixados e o fluxo de lote renomeia e remove no fim."""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

at._buscar_nyaa = lambda consulta: []  # sem rede: reserva do nyaa desligada nos testes

URL = "https://darkmahou.io/anime/100-nin/"
HASH = "9" * 40
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=%5BDKB%5D%20Anime%20100-nin%20-%20%28Season%2001%29%20%5BBatch%5D"
HTML = (f'<div class="soraddl dlone"><h3>Anime 100-nin no Kanojo</h3><table><tr><td>Baixar</td>'
        f'<td><a href="https://ln5.sync.com/dl/x">1080p</a><a href="{MAGNET}">1080p HEVC</a></td></tr></table></div>')
PASTA_TORRENT = "[DKB] Anime 100-nin (Season 01)"


class _ClienteFalso:
    def __init__(self, pasta):
        self.pasta, self.arquivos, self.progress = pasta, [], 0.0
        self.adicionados, self.iniciados, self.removidos = [], [], []

    def torrents_add(self, urls=None, **kw):
        self.adicionados.append((at._hash_do_magnet(urls), kw.get("stop_condition")))

    def torrents_files(self, torrent_hash):
        return self.arquivos

    def torrents_file_priority(self, torrent_hash, file_ids, priority):
        for f in self.arquivos:
            if f.index in file_ids:
                f.priority = priority

    def torrents_start(self, torrent_hashes):
        self.iniciados.append(torrent_hashes)

    def torrents_info(self, torrent_hashes):
        return [SimpleNamespace(hash=torrent_hashes, progress=self.progress, save_path=self.pasta, content_path="")]

    def torrents_rename_file(self, torrent_hash, old_path, new_path):
        os.rename(os.path.join(self.pasta, old_path), os.path.join(self.pasta, new_path))

    def torrents_delete(self, delete_files, torrent_hashes):
        self.removidos.append(torrent_hashes)


def testar_pacote_completo_vira_episodios_pela_lista_de_arquivos():
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump({"anime": {"titulo": "Anime", "url": URL, "interesse": "tenho_interesse",
                             "ultimo_episodio_visto": None, "episodios": {"2": "assistido"},
                             "downloads_em_andamento": {}}}, f)
    at.qbittorrent_configurado = lambda: True
    at.obter_anime_pasta_downloads = lambda: pasta
    at._obter_html_darkmahou = lambda url: HTML
    cliente = _ClienteFalso(pasta)
    at._cliente_qbittorrent = lambda: cliente

    qtd, disparados, _ = at._baixar_pendentes_do_registro("anime", at._carregar_animes()["anime"])
    assert (qtd, disparados) == (1, ["pacote da temporada"])
    assert cliente.adicionados == [(HASH, "MetadataReceived")]
    assert at._baixar_pendentes_do_registro("anime", at._carregar_animes()["anime"])[0] == 0  # não repete

    at.verificar_downloads_em_andamento()  # sem metadado ainda: espera
    assert at._carregar_animes()["anime"]["pacote_completo"]["aguardando_arquivos"] is True

    os.makedirs(os.path.join(pasta, PASTA_TORRENT))
    nomes = [f"{PASTA_TORRENT}/[DKB] Anime - 0{n} [1080p].mkv" for n in (1, 2, 3)]
    cliente.arquivos = [SimpleNamespace(index=i, name=n, priority=1) for i, n in enumerate(nomes)]
    at.verificar_downloads_em_andamento()
    salvo = at._carregar_animes()["anime"]
    assert sorted(salvo["downloads_em_andamento"]) == ["1", "3"]  # E02 já assistido
    assert salvo["ultimo_episodio_visto"] == 3
    assert [f.priority for f in cliente.arquivos] == [1, 0, 1] and cliente.iniciados == [HASH]

    for n in (nomes[0], nomes[2]):
        open(os.path.join(pasta, n), "w").close()
    cliente.progress = 1.0
    at.verificar_downloads_em_andamento()
    salvo = at._carregar_animes()["anime"]
    assert os.path.isfile(os.path.join(pasta, "Anime - S01E01.mkv"))
    assert os.path.isfile(os.path.join(pasta, "Anime - S01E03.mkv"))
    assert salvo["downloads_em_andamento"] == {} and cliente.removidos == [HASH]
    assert salvo["episodios"] == {"1": "baixado", "2": "assistido", "3": "baixado"}


if __name__ == "__main__":
    testar_pacote_completo_vira_episodios_pela_lista_de_arquivos()
    print("OK")

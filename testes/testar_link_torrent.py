"""Teste isolado (sem rede, sem qBittorrent real): link .torrent do nyaa.si
(páginas antigas do DarkMahou) vira opção de download, o hash sai do
dicionário `info`, e .torrent removido (404) sai da lista de candidatos."""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

URL = "https://darkmahou.io/anime/antigo/"
NOME = b"[Erai-raws] Antigo - 06 [1080p][Multi].mkv"
INFO = b"d6:lengthi10e4:name%d:%s12:piece lengthi16384ee" % (len(NOME), NOME)
TORRENT = b"d8:announce14:http://tracker4:info" + INFO + b"e"
HASH = hashlib.sha1(INFO).hexdigest()
LINK_OK, LINK_404 = "https://nyaa.si/download/2.torrent", "https://nyaa.si/download/1.torrent"

HTML = f"""<div class="soraddl"><h3>Episódio 06</h3><table><tr><td>Baixar</td>
<td><a href="{LINK_404}">1080p HEVC</a><a href="{LINK_OK}">1080p</a></td></tr></table></div>"""


class _Resposta:
    def __init__(self, url):
        self.status_code = 200 if url == LINK_OK else 404
        self.content = TORRENT if url == LINK_OK else b""

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"{self.status_code} NOT FOUND")


class _ClienteFalso:
    def __init__(self):
        self.adicionados = []

    def torrents_add(self, **kw):
        self.adicionados.append(kw)


def testar_link_torrent_baixa_e_404_fica_de_fora():
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump({"antigo": {"titulo": "Antigo", "url": URL, "interesse": "tenho_interesse",
                              "episodios": {}, "downloads_em_andamento": {}}}, f)
    at.qbittorrent_configurado = lambda: True
    at.obter_anime_pasta_downloads = lambda: pasta
    at._obter_html_darkmahou = lambda url: HTML
    at.requests.get = lambda url, **kw: _Resposta(url)
    at._cache_torrents.clear()
    cliente = _ClienteFalso()
    at._cliente_qbittorrent = lambda: cliente

    assert [o[1] for o in at._extrair_opcoes_download(URL, 6)] == [LINK_404, LINK_OK]
    assert at._hash_da_opcao(LINK_OK) == HASH
    assert at._nome_da_opcao(LINK_OK).startswith("[Erai-raws] Antigo - 06")
    assert at.baixar_episodio("antigo", at._carregar_animes()["antigo"], 6)
    assert len(cliente.adicionados) == 1 and cliente.adicionados[0]["torrent_files"] == TORRENT
    assert "urls" not in cliente.adicionados[0]
    assert at._carregar_animes()["antigo"]["downloads_em_andamento"]["6"]["hash"] == HASH


if __name__ == "__main__":
    testar_link_torrent_baixa_e_404_fica_de_fora()
    print("OK")

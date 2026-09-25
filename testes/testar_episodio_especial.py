"""Teste isolado (sem rede, sem qBittorrent real): bloco "Episódio
Especial" (sem número) vira "especial-K" com posição pelo episódio anterior,
baixa, é renomeado para "S01E06.5 - Especial 1" e o estado fica em
`especiais` - fora de `episodios`, cujas chaves a GAIA converte com int()."""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

URL = "https://darkmahou.io/anime/especial/"
HASH_SP, HASH_E07 = "f" * 40, "1" * 40
MAGNET_SP = f"magnet:?xt=urn:btih:{HASH_SP}&dn=%5BErai-raws%5D%20Anime%20-%20SP1%20%5B1080p%5D.mkv"
MAGNET_E07 = f"magnet:?xt=urn:btih:{HASH_E07}&dn=%5BErai-raws%5D%20Anime%20-%2007%20%5B1080p%5D.mkv"


def _bloco(titulo, magnet):
    return (f'<div class="soraddl"><h3>{titulo}</h3><table><tr><td>Baixar</td>'
            f'<td><a href="{magnet}">1080p</a></td></tr></table></div>')


HTML = _bloco("Episódio 06", MAGNET_E07) + _bloco("Episódio Especial", MAGNET_SP) + _bloco("Episódio 07", MAGNET_E07)


class _ClienteFalso:
    def __init__(self, pasta):
        self.pasta, self.adicionados, self.removidos, self.progress = pasta, [], [], 0.0

    def torrents_add(self, urls=None, **kw):
        self.adicionados.append(at._hash_do_magnet(urls))

    def torrents_info(self, torrent_hashes):
        caminho = os.path.join(self.pasta, "[Erai-raws] Anime - SP1 [1080p].mkv")
        return [SimpleNamespace(hash=torrent_hashes, progress=self.progress, save_path=self.pasta, content_path=caminho)]

    def torrents_delete(self, delete_files, torrent_hashes):
        self.removidos.append(torrent_hashes)


def testar_especial_baixa_e_renomeia_pela_posicao():
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump({"anime": {"titulo": "Anime", "url": URL, "interesse": "tenho_interesse",
                             "ultimo_episodio_visto": 7,
                             "episodios": {str(n): "baixado" for n in range(1, 8)},
                             "downloads_em_andamento": {}}}, f)
    at.qbittorrent_configurado = lambda: True
    at.obter_anime_pasta_downloads = lambda: pasta
    at._obter_html_darkmahou = lambda url: HTML
    cliente = _ClienteFalso(pasta)
    at._cliente_qbittorrent = lambda: cliente

    qtd, disparados, falhos = at._baixar_pendentes_do_registro("anime", at._carregar_animes()["anime"])
    assert (qtd, disparados, falhos) == (1, ["6.5 - Especial 1"], []), (qtd, disparados, falhos)
    assert cliente.adicionados == [HASH_SP]
    salvo = at._carregar_animes()["anime"]
    assert salvo["especiais"] == {"1": {"apos": 6}}
    assert "especial-1" in salvo["downloads_em_andamento"]
    # Segunda checagem: nada de novo pra baixar.
    assert at._baixar_pendentes_do_registro("anime", at._carregar_animes()["anime"])[0] == 0

    open(os.path.join(pasta, "[Erai-raws] Anime - SP1 [1080p].mkv"), "w").close()
    cliente.progress = 1.0
    at.verificar_downloads_em_andamento()
    salvo = at._carregar_animes()["anime"]
    assert os.path.isfile(os.path.join(pasta, "Anime - S01E06.5 - Especial 1.mkv"))
    assert salvo["especiais"]["1"]["status"] == "baixado"
    assert set(salvo["episodios"]) == {str(n) for n in range(1, 8)}  # sem chave não numérica
    assert salvo["downloads_em_andamento"] == {} and cliente.removidos == [HASH_SP]
    assert at._PADRAO_NOME_ESPECIAL.search("Anime - S01E06.5 - Especial 1.mkv")


if __name__ == "__main__":
    testar_especial_baixa_e_renomeia_pela_posicao()
    print("OK")

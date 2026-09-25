"""Teste isolado (sem rede, sem qBittorrent real): página de filme com bloco
único "Filme Completo Legendado Torrent" (caso real Boku no Kokoro no Yabai
Yatsu Movie, 2026-09-25). Antes caía no pacote da temporada e era descartado
por não ter vídeo numerado; agora é o episódio 1 e o registro ganha
`filme = True` (o Painel pula a seleção de episódios)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

at._buscar_nyaa = lambda consulta: []  # sem rede: reserva do nyaa desligada nos testes

URL = "https://darkmahou.io/anime-movie/"
HASH = "5" * 40
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=%5BMEMESUB%5D%20Anime%20Movie%20%5B1080p%5D.mkv"
HTML = ('<h1>Anime Movie</h1><div class="soraddl dlone"><h3>Filme Completo Legendado Torrent</h3>'
        f'<table><tr><td>Baixar</td><td><a href="{MAGNET}">1080p</a></td></tr></table></div>')


class _ClienteFalso:
    def __init__(self):
        self.adicionados = []

    def torrents_add(self, urls=None, **kw):
        self.adicionados.append((at._hash_do_magnet(urls), kw.get("stop_condition")))


def testar_filme_vira_episodio_1_sem_pacote():
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump({}, f)
    at.qbittorrent_configurado = lambda: True
    at.obter_anime_pasta_downloads = lambda: pasta
    at._obter_html_darkmahou = lambda url: HTML
    cliente = _ClienteFalso()
    at._cliente_qbittorrent = lambda: cliente

    chave, erro = at.adicionar_anime_manual(URL)
    registro = at._carregar_animes()[chave]
    assert erro is None and registro["filme"] is True and registro["ultimo_episodio_visto"] == 1

    disparados, aviso = at.baixar_pendentes_com_aviso(chave)
    salvo = at._carregar_animes()[chave]
    assert (disparados, aviso) == (1, None)
    assert cliente.adicionados == [(HASH, None)]  # episódio normal, não pacote parado no metadado
    assert list(salvo["downloads_em_andamento"]) == ["1"] and "pacote_completo" not in salvo


if __name__ == "__main__":
    testar_filme_vira_episodio_1_sem_pacote()
    print("OK")

"""Teste isolado (sem rede, sem qBittorrent real): lote sem censura
("Episódios 01~04 Sem Censura") entra como opção, baixa só os arquivos
pedidos, compartilha o torrent entre episódios, renomeia com sufixo
" [Sem Censura]" e só remove o torrent depois do último episódio."""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

at._buscar_nyaa = lambda consulta: []  # sem rede: reserva do nyaa desligada nos testes

URL = "https://darkmahou.io/anime/teste/"
HASH_LOTE, HASH_JUDAS = "d" * 40, "e" * 40
MAGNET_LOTE = f"magnet:?xt=urn:btih:{HASH_LOTE}&dn=%5BWF%5D%20Teste%20%2801-04%29%20%5BUNCENSORED%5D"
MAGNET_JUDAS = f"magnet:?xt=urn:btih:{HASH_JUDAS}&dn=%5BJudas%5D%20Teste%20-%20S01E02%20%5B1080p%5D"

HTML = f"""<html><body>
<div class="soraddl"><h3>Episódios 01~04 Sem Censura</h3><table><tr><td>WF</td>
<td><a href="{MAGNET_LOTE}">1080p</a></td></tr></table></div>
<div class="soraddl"><h3>Episódio 02</h3><table><tr><td>Baixar</td>
<td><a href="{MAGNET_JUDAS}">1080p HEVC</a></td></tr></table></div>
<div class="soraddl"><h3>Episódio 05</h3><table><tr><td>Baixar</td>
<td><a href="{MAGNET_JUDAS}">1080p HEVC</a></td></tr></table></div>
</body></html>"""


class _ClienteFalso:
    def __init__(self, pasta):
        self.pasta = pasta
        self.adicionados, self.removidos, self.iniciados, self.renomeados = [], [], [], []
        self.arquivos = []
        self.progress = 0.0

    def torrents_add(self, urls, **kw):
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
        if torrent_hashes in self.removidos:
            return []
        return [SimpleNamespace(hash=torrent_hashes, progress=self.progress, save_path=self.pasta, content_path="")]

    def torrents_rename_file(self, torrent_hash, old_path, new_path):
        self.renomeados.append(os.path.basename(new_path))
        os.rename(os.path.join(self.pasta, old_path), os.path.join(self.pasta, new_path))

    def torrents_delete(self, delete_files, torrent_hashes):
        assert delete_files is False
        self.removidos.append(torrent_hashes)


def _preparar(registro):
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump({"teste": registro}, f)
    at.qbittorrent_configurado = lambda: True
    at.obter_anime_pasta_downloads = lambda: pasta
    at._obter_html_darkmahou = lambda url: HTML
    return pasta


def testar_lote_so_entra_como_opcao_do_episodio_coberto():
    _preparar({})
    opcoes = at._extrair_opcoes_download(URL, 2)
    assert opcoes == [("1080p HEVC", MAGNET_JUDAS, None),
                      ("1080p (Episódios 01~04 Sem Censura)", MAGNET_LOTE, (1, 4))], opcoes
    assert at._escolher_melhor_magnet(opcoes, 2) == MAGNET_LOTE
    assert at._extrair_opcoes_download(URL, 5) == [("1080p HEVC", MAGNET_JUDAS, None)]
    assert at._intervalo_do_lote("1ª Temporada Completa Legendado Torrent [01-12]") == (1, 12)
    # Episódio avulso sem censura ganha de lote sem censura no mesmo nível.
    avulso = ("1080p Sem Censura", MAGNET_JUDAS, None)
    assert at._escolher_melhor_magnet([("1080p", MAGNET_LOTE, (1, 12)), avulso], 2) == MAGNET_JUDAS


def testar_lote_compartilhado_baixa_so_pedidos_e_renomeia_com_sufixo():
    pasta = _preparar({"titulo": "Teste", "url": URL, "interesse": "tenho_interesse",
                       "episodios": {"1": "baixado", "2": "assistido"}, "downloads_em_andamento": {}})
    cliente = _ClienteFalso(pasta)
    at._cliente_qbittorrent = lambda: cliente

    registro = at._carregar_animes()["teste"]
    assert at.baixar_episodio("teste", registro, 1) is False  # já baixado, sem a exceção
    assert at.baixar_episodio("teste", registro, 1, rebaixar_sem_censura=True)
    assert at.baixar_episodio("teste", at._carregar_animes()["teste"], 2, rebaixar_sem_censura=True)
    assert cliente.adicionados == [(HASH_LOTE, "MetadataReceived")], cliente.adicionados

    # Metadado chegou: só E01/E02 ficam ligados, torrent retomado.
    pasta_lote = os.path.join(pasta, "[WF] Teste (01-04)")
    os.makedirs(pasta_lote)
    nomes = [f"[WF] Teste (01-04)/[WF] Teste - 0{n} [UNCENSORED].mkv" for n in range(1, 5)]
    cliente.arquivos = [SimpleNamespace(index=i, name=n, priority=1) for i, n in enumerate(nomes)]
    at.verificar_downloads_em_andamento()
    assert [f.priority for f in cliente.arquivos] == [1, 1, 0, 0]
    assert cliente.iniciados == [HASH_LOTE]
    at.verificar_downloads_em_andamento()
    assert cliente.iniciados == [HASH_LOTE]  # idempotente

    for n in nomes[:2]:
        open(os.path.join(pasta, n), "w").close()
    cliente.progress = 1.0
    at.verificar_downloads_em_andamento()
    salvo = at._carregar_animes()["teste"]
    assert sorted(cliente.renomeados) == ["Teste - S01E01 [Sem Censura].mkv", "Teste - S01E02 [Sem Censura].mkv"]
    assert cliente.removidos == [HASH_LOTE]  # uma vez só, depois do último
    assert os.path.isfile(os.path.join(pasta, "Teste - S01E01 [Sem Censura].mkv"))  # na raiz, junto dos outros
    assert not os.path.exists(pasta_lote)  # subpasta vazia removida
    assert salvo["downloads_em_andamento"] == {}
    assert salvo["episodios"] == {"1": "baixado", "2": "assistido"}
    assert at._PADRAO_NOME_ARQUIVO.match("Teste - S01E02 [Sem Censura].mkv").group(2) == "02"


if __name__ == "__main__":
    testar_lote_so_entra_como_opcao_do_episodio_coberto()
    testar_lote_compartilhado_baixa_so_pedidos_e_renomeia_com_sufixo()
    print("OK")

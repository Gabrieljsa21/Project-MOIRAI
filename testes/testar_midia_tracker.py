"""Teste isolado (sem rede, sem qBittorrent real) das séries e filmes do
TorrentFilmesHD (2026-10-17, core/midia_tracker.py): leitura da home e da
página, prioridade dual > dublado > resto com a melhor resolução, CAM fora,
fluxo pendente -> 👍 -> download -> renomeação e troca de torrent travado."""
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at
from moirai.core import midia_tracker as mt

BASE = mt.URL_BASE
URL_SERIE = f"{BASE}/slow-horses-6a-temporada-2026/"
URL_FILME = f"{BASE}/verity-2026/"


def _hash(n):
    return str(n) * 40


def _botao(rotulo, n):
    return f'<a class="download-btn" href="/link.php?id={n}"><span class="download-btn-label">{rotulo}</span></a>'


HOME = (
    '<section class="filmes-section"><div class="listagem">'
    f'<article class="item"><a href="{URL_FILME}" title="Verity"><div class="imagem"><img src="{BASE}/capa-verity.jpg"></div>'
    '<div class="titulo"><span>Verity (2026)</span></div></a></article></div></section>'
    '<section class="filmes-section"><div class="listagem">'
    f'<article class="item"><a href="{URL_SERIE}" title="Slow Horses"><div class="imagem"><img src="{BASE}/capa-sh.jpg"></div>'
    '<div class="titulo"><span>Slow Horses 6ª Temporada (2026)</span></div></a></article>'
    f'<article class="item"><a href="{URL_FILME}"><div class="titulo"><span>Verity (2026)</span></div></a></article>'
    '</div></section>'
)
PAGINAS = {
    URL_SERIE: ('<h1 class="titulo">Slow Horses 6ª Temporada (2026) Torrent Dual Áudio Download</h1>'
                + _botao("S06.720p | Ep.01 | Dual Áudio", 1)
                + _botao("S06.1080p | Ep.01 | Dublado", 2)
                + _botao("S06.1080p | MKV | Ep.02 | Dual Áudio", 3)
                + _botao("S06.2160p | MKV | Ep.02 | Dual Áudio", 4)
                + _botao("S06.1080p | 9 GB | Dual Áudio", 5)),
    URL_FILME: ('<h1 class="titulo">Verity (2026) Torrent Dual Áudio Download</h1>'
                + _botao("1080p.Tenesync | MKV | 4.79 GB | Inglês", 6)),
}


class _Torrent(SimpleNamespace):
    pass


class _ClienteFalso:
    def __init__(self):
        self.torrents = {}
        self.removidos = []

    def torrents_add(self, urls=None, save_path=None, category=None):
        self.torrents[at._hash_do_magnet(urls)] = _Torrent(
            hash=at._hash_do_magnet(urls), progress=0.0, num_seeds=1, save_path=save_path, content_path=None)

    def torrents_info(self, torrent_hashes=None):
        return [self.torrents[torrent_hashes]] if torrent_hashes in self.torrents else []

    def torrents_set_location(self, location=None, torrent_hashes=None):
        self.torrents[torrent_hashes].save_path = location

    def torrents_delete(self, delete_files=False, torrent_hashes=None):
        assert delete_files is False
        self.removidos.append(torrent_hashes)
        self.torrents.pop(torrent_hashes, None)


def _preparar():
    pasta = tempfile.mkdtemp()
    mt.ARQUIVO_TITULOS = os.path.join(pasta, "titulos.json")
    mt._cache_html.clear()
    mt._obter_html = lambda url: HOME if url == BASE + "/" else PAGINAS[url]
    mt.resolver_magnet = lambda link, url_pagina: f"magnet:?xt=urn:btih:{_hash(link.rsplit('=', 1)[1])}"
    mt.obter_midia_pasta_series = lambda: os.path.join(pasta, "Series")
    mt.obter_midia_pasta_filmes = lambda: os.path.join(pasta, "Filmes")
    mt.obter_anime_download_travado_horas = lambda: 24
    at.qbittorrent_configurado = lambda: True
    cliente = _ClienteFalso()
    at._cliente_qbittorrent = lambda: cliente
    return pasta, cliente


def testar_rotulos_e_prioridade():
    r = mt.interpretar_rotulo("S02.1080p | Episódio 10 Final | Multi Áudio")
    assert (r["episodio"], r["temporada"], r["resolucao"], r["audio"], r["cam"]) == (10, 2, 1080, 2, False)
    assert mt.interpretar_rotulo("S06.1080p | MKV | Ep.03 | Dual Áudio")["episodio"] == 3
    pacote = mt.interpretar_rotulo("S01.1080p | 6.41 GB | Dual Áudio")
    assert pacote["episodio"] is None and pacote["temporada"] == 1 and pacote["tamanho_gb"] == 6.41
    assert mt.interpretar_rotulo("CAMRip | 5.11 GB | Inglês")["cam"]
    assert mt.interpretar_rotulo("1080p.Tenesync | MKV | 4.79 GB | Inglês")["cam"]
    # Pedido do usuário: dual primeiro, mesmo com resolução menor; depois dublado.
    dual_720 = mt.interpretar_rotulo("720p | Dual Áudio")
    dub_1080 = mt.interpretar_rotulo("1080p | Dublado")
    leg_2160 = mt.interpretar_rotulo("2160p | Legendado")
    assert max([leg_2160, dub_1080, dual_720], key=mt.chave_qualidade) is dual_720
    assert max([leg_2160, dub_1080], key=mt.chave_qualidade) is dub_1080
    assert mt.titulo_base_serie("Slow Horses 6ª Temporada (2026)") == "Slow Horses"
    assert mt.tipo_do_slug("slow-horses-6a-temporada-2026") == "serie" and mt.tipo_do_slug("verity-2026") == "filme"


def testar_fluxo_pendente_like_download_renomeacao():
    pasta, cliente = _preparar()
    resultado = mt.verificar_lancamentos()
    titulos = dict(mt.obter_titulos_rastreados())
    assert resultado["itens_home"] == 2 and len(resultado["novos"]) == 2 and not resultado["disparados"]
    assert {r["interesse"] for r in titulos.values()} == {"pendente"} and not cliente.torrents

    # Série: ep 1 dual 720p (não o dublado 1080p), ep 2 dual 2160p; o pacote fica de fora.
    mt.marcar_interesse("slow-horses-6a-temporada-2026", "tenho_interesse")
    assert mt.baixar_pendentes_de("slow-horses-6a-temporada-2026") == (2, None)
    assert set(cliente.torrents) == {_hash(1), _hash(4)}
    serie = dict(mt.obter_titulos_rastreados())["slow-horses-6a-temporada-2026"]
    # Pedido do usuário: sem pasta por temporada, o vídeo fica direto na pasta da série.
    pasta_temporada = os.path.join(pasta, "Series", "Slow Horses")
    assert serie["downloads_em_andamento"]["1"]["pasta"] == pasta_temporada

    # Filme só com telesync: não baixa e avisa.
    mt.marcar_interesse("verity-2026", "tenho_interesse")
    disparados, aviso = mt.baixar_pendentes_de("verity-2026")
    assert disparados == 0 and "CAM" in aviso and len(cliente.torrents) == 2

    # Conclusão do ep 1: o vídeo sai da pasta do torrent para a pasta da série.
    subpasta = os.path.join(pasta_temporada, "Slow.Horses.S06E01.1080p")
    os.makedirs(subpasta)
    with open(os.path.join(subpasta, "Slow.Horses.S06E01.mkv"), "wb") as f:
        f.write(b"x" * 10)
    for sobra in ("Acesse -StarckFilmes.png", "GRUPO TELEGRAM.url"):  # propaganda do release: apagada
        open(os.path.join(subpasta, sobra), "wb").close()
    cliente.torrents[_hash(1)].progress = 1.0
    cliente.torrents[_hash(1)].content_path = subpasta
    assert mt.verificar_downloads() == 1
    assert os.path.exists(os.path.join(pasta_temporada, "Slow Horses - S06E01.mkv"))
    assert not os.path.exists(subpasta) and cliente.removidos == [_hash(1)]
    serie = dict(mt.obter_titulos_rastreados())["slow-horses-6a-temporada-2026"]
    assert serie["episodios"]["1"] == "baixado" and list(serie["downloads_em_andamento"]) == ["2"]

    # Próxima checagem não baixa de novo o que já foi.
    assert mt.verificar_lancamentos()["disparados"] == []

    # Download que começou na pasta antiga (com "Temporada N") é movido pelo qBittorrent.
    pasta_antiga = os.path.join(pasta_temporada, "Temporada 6")
    cliente.torrents[_hash(4)].save_path = pasta_antiga
    mt.verificar_downloads()
    assert cliente.torrents[_hash(4)].save_path == pasta_temporada

    # Com vídeo ainda dentro, a pasta do torrent nunca é apagada.
    outra = os.path.join(pasta_temporada, "Outro")
    os.makedirs(outra)
    open(os.path.join(outra, "extra.mkv"), "wb").close()
    mt._apagar_sobras(_Torrent(content_path=outra, save_path=pasta_temporada))
    assert os.path.exists(os.path.join(outra, "extra.mkv"))

    # 👎 enxuga o registro.
    mt.marcar_interesse("verity-2026", "sem_interesse")
    assert set(dict(mt.obter_titulos_rastreados())["verity-2026"]) <= set(mt._CAMPOS_MINIMOS_SEM_INTERESSE)


def testar_travado_troca_pela_proxima_opcao():
    pasta, cliente = _preparar()
    mt.verificar_lancamentos()
    mt.marcar_interesse("slow-horses-6a-temporada-2026", "tenho_interesse")
    mt.baixar_pendentes_de("slow-horses-6a-temporada-2026")
    titulos = mt._carregar()
    for info in titulos["slow-horses-6a-temporada-2026"]["downloads_em_andamento"].values():
        info["progresso_em"] = "2026-01-01 00:00"
    mt._salvar(titulos)
    cliente.torrents[_hash(1)].num_seeds = 0
    cliente.torrents[_hash(4)].num_seeds = 0
    mt.verificar_downloads()
    serie = mt._carregar()["slow-horses-6a-temporada-2026"]
    # Ep 1 tinha outra opção (dublado 1080p): trocou. Ep 2 só tem 2 opções dual; trocou pela 1080p.
    assert serie["downloads_em_andamento"]["1"]["opcao"] == "S06.1080p | Ep.01 | Dublado"
    assert serie["downloads_em_andamento"]["2"]["opcao"] == "S06.1080p | MKV | Ep.02 | Dual Áudio"
    assert set(cliente.removidos) == {_hash(1), _hash(4)}
    # Sem mais opção: o torrent fica.
    titulos = mt._carregar()
    for info in titulos["slow-horses-6a-temporada-2026"]["downloads_em_andamento"].values():
        info["progresso_em"] = "2026-01-01 00:00"
    mt._salvar(titulos)
    cliente.torrents[_hash(2)].num_seeds = 0
    mt.verificar_downloads()
    serie = mt._carregar()["slow-horses-6a-temporada-2026"]
    assert serie["downloads_em_andamento"]["1"]["sem_alternativa"] and _hash(2) in cliente.torrents


def testar_hash_base32_e_torrent_com_dois_episodios():
    """Caso real VisionQuest (2026-10-17): magnet com hash em base32 e um
    botão "Ep. 01 e 02" davam "todas as opções da página já travaram"."""
    base32 = "magnet:?xt=urn:btih:a5dpkmtc4pv56cogiptfz76sct5s2tcz&dn=VisionQuest.S01E01-02.1080p"
    assert mt.hash_do_magnet(base32) == "0746f53262e3ebdf09c643e65cffd214fb2d4c59"
    assert mt.hash_do_magnet(f"magnet:?xt=urn:btih:{'A' * 40}") == "a" * 40
    # Magnet direto quebrado pelo site (caso real A Casa do Dragão S03E02).
    quebrado = "magnet:?xt=urnbtih3ed507635a30562c5a75272b4dede0fb66890e15&tr=udptracker.opentrackr.org1337announce"
    assert mt.hash_do_magnet(quebrado) == "3ed507635a30562c5a75272b4dede0fb66890e15"
    reparado = mt._reparar_magnet(quebrado)
    assert reparado.startswith("magnet:?xt=urn:btih:3ed507635a30562c5a75272b4dede0fb66890e15&tr=udp://")
    assert mt._reparar_magnet(base32) == base32
    assert mt.interpretar_rotulo("S01.1080p | MKV | Ep. 01 e 02 | Dual Áudio")["episodios"] == [1, 2]
    assert mt.interpretar_rotulo("S01.1080p | Episódios 01 a 03 | Dual Áudio")["episodios"] == [1, 2, 3]
    assert mt.interpretar_rotulo("S06.1080p | MKV | Ep.05 - 720p")["episodios"] == [5]
    assert mt.interpretar_rotulo("S13.1080p | MKV | Ep.04/06 | Dual Áudio")["episodios"] == [4, 5, 6]

    pasta, cliente = _preparar()
    url = f"{BASE}/visionquest-1a-temporada-2026/"
    PAGINAS[url] = ('<h1 class="titulo">VisionQuest 1ª Temporada (2026) Torrent Dual Áudio Download</h1>'
                    + _botao("S01.1080p | MKV | Ep. 01 e 02 | Dual Áudio", 7)
                    + _botao("S01.1080p | MKV | Ep.03 | Dual Áudio", 8))
    chave, erro = mt.adicionar_por_link(url)
    assert erro is None
    assert mt.baixar_pendentes_de(chave) == (2, None)
    serie = mt._carregar()[chave]
    assert set(serie["downloads_em_andamento"]) == {"1-2", "3"}
    assert mt.baixar_pendentes_de(chave)[0] == 0  # nada repetido

    # Um arquivo com os dois episódios juntos.
    pasta_temporada = os.path.join(pasta, "Series", "VisionQuest")
    os.makedirs(pasta_temporada, exist_ok=True)
    with open(os.path.join(pasta_temporada, "VisionQuest.S01E01-02.1080p.mkv"), "wb") as f:
        f.write(b"x")
    cliente.torrents[_hash(7)].progress = 1.0
    cliente.torrents_files = lambda torrent_hash=None: [SimpleNamespace(name="VisionQuest.S01E01-02.1080p.mkv")]
    assert mt.verificar_downloads() == 1
    assert os.path.exists(os.path.join(pasta_temporada, "VisionQuest - S01E01-E02.mkv"))
    serie = mt._carregar()[chave]
    assert serie["episodios"]["1"] == serie["episodios"]["2"] == "baixado"
    assert list(serie["downloads_em_andamento"]) == ["3"]


if __name__ == "__main__":
    testar_hash_base32_e_torrent_com_dois_episodios()
    testar_rotulos_e_prioridade()
    testar_fluxo_pendente_like_download_renomeacao()
    testar_travado_troca_pela_proxima_opcao()
    print("OK")

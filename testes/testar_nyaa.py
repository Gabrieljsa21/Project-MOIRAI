"""Teste isolado (sem rede): busca reserva no nyaa.si quando a página do
DarkMahou não tem mais opção (2026-09-25). Só entra o que mantém o padrão do
MOIRAI: legenda em português, mesma temporada e episódio, com seeder, sem
dublagem nem batch."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

RESULTADOS = [
    ("[Erai-raws] Megami no Cafe Terrace 2nd Season - 10 [1080p][Multiple Subtitle] [ENG][POR-BR]", "https://nyaa.si/download/1.torrent", 3),
    ("[Erai-raws] Megami no Cafe Terrace - 10 [1080p][Multiple Subtitle] [ENG][POR-BR]", "https://nyaa.si/download/2.torrent", 9),  # temporada 1
    ("[Erai-raws] Megami no Cafe Terrace 2nd Season - 11 [1080p][Multiple Subtitle]", "https://nyaa.si/download/3.torrent", 5),  # outro episódio
    ("[SubsPlease] Megami no Cafe Terrace S2 - 10 (1080p) [ABCD1234].mkv", "https://nyaa.si/download/4.torrent", 30),  # só inglês
    ("[Yameii] Megami no Cafe Terrace S2 - 10 [English Dub] [POR-BR]", "https://nyaa.si/download/5.torrent", 4),  # dublado
    ("[Erai-raws] Megami no Cafe Terrace 2nd Season - 10 [720p][Multiple Subtitle]", "https://nyaa.si/download/6.torrent", 0),  # sem seeder
    ("[Erai-raws] Megami no Cafe Terrace 2nd Season - 10 END [480p][Multiple Subtitle]", "https://nyaa.si/download/7.torrent", 1),
]


def testar_filtros_da_busca_no_nyaa():
    consultas = []
    at._buscar_nyaa = lambda consulta: consultas.append(consulta) or RESULTADOS
    registro = {"titulo": "Megami no Café Terrace 2ª Temporada", "url": "x"}
    links = [o[1] for o in at._opcoes_nyaa(registro, 10)]
    assert consultas == ["megami no cafe terrace 10"], consultas
    assert links == ["https://nyaa.si/download/1.torrent", "https://nyaa.si/download/7.torrent"], links
    assert at._opcoes_nyaa(registro, "especial-1") == []


def testar_nyaa_so_entra_sem_opcao_na_pagina():
    registro = {"titulo": "Anime", "url": "x", "episodios_magnets_tentados": {"3": ["a" * 40]}}
    at._obter_torrent = lambda link: (b"", link[-40:], "")
    at._opcoes_nyaa = lambda reg, n: [("nyaa", "https://nyaa.si/" + "b" * 40, None)]
    at._extrair_opcoes_download = lambda url, n: [("1080p", "magnet:?xt=urn:btih:" + "c" * 40, None)]
    assert [o[0] for o in at._opcoes_nao_tentadas(registro, 3)[1]] == ["1080p"]
    at._extrair_opcoes_download = lambda url, n: [("1080p", "magnet:?xt=urn:btih:" + "a" * 40, None)]
    total, opcoes = at._opcoes_nao_tentadas(registro, 3)
    assert total == 2 and [o[0] for o in opcoes] == ["nyaa"]


def testar_espelho_quando_nyaa_si_nao_responde():
    from types import SimpleNamespace
    pedidos = []

    def get(url, **kw):
        pedidos.append(url)
        if url.startswith("https://nyaa.si"):
            raise at.requests.Timeout("sem resposta")
        return SimpleNamespace(raise_for_status=lambda: None, content=b"ok")

    original, at.requests.get = at.requests.get, get
    at._nyaa_si_fora_ate[0] = 0.0
    try:
        assert at._get_nyaa_com_espelho("https://nyaa.si/download/1.torrent", None).content == b"ok"
        assert at._get_nyaa_com_espelho("https://nyaa.si/download/2.torrent", None).content == b"ok"
        # Depois da 1ª falha, o nyaa.si é pulado direto.
        assert pedidos == ["https://nyaa.si/download/1.torrent", "https://nyaa.land/download/1.torrent",
                           "https://nyaa.land/download/2.torrent"], pedidos
    finally:
        at.requests.get = original
        at._nyaa_si_fora_ate[0] = 0.0


if __name__ == "__main__":
    testar_filtros_da_busca_no_nyaa()
    testar_nyaa_so_entra_sem_opcao_na_pagina()
    testar_espelho_quando_nyaa_si_nao_responde()
    print("OK")

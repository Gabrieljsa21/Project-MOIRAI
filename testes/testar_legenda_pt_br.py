"""Teste isolado (sem rede): preferência por release com legenda pt-BR
(2026-11-07, caso real Tougen Anki: Nikko Kegon no Taki-hen E06, baixado do
rip da VARYG, que não tem português, quando a Erai-raws com pt-BR já
existia)."""
import os
import sys
import urllib.parse
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

DESCRICOES = {
    # Erai-raws: lista com Portuguese (Brazil).
    "100": "Subtitles Info:` **English** (CR_English) | ASS **Portuguese** (CR_Portuguese(Brazil)) | ASS",
    # VARYG: lista de 12 legendas, nenhuma em português.
    "200": "Subtitles (12):` **English**, ASS │ **German**, ASS │ **Spanish** (Latin American), ASS",
    # Judas: descrição sem lista de legendas.
    "300": "Encoded by Judas. Join our discord.",
}


def _magnet(letra, nome):
    return f"magnet:?xt=urn:btih:{letra * 40}&dn={urllib.parse.quote(nome)}"


VARYG = _magnet("a", "TOUGEN ANKI S02E06 Out of Focus 1080p CR WEB-DL AAC2.0 H.264-VARYG (Multi-Subs)")
ERAI_AVC = _magnet("b", "[Erai-raws] Tougen Anki Nikko Kegon no Taki-hen - 06 [1080p CR WEB-DL AVC AAC][MultiSub][5C6C4B4F]")
JUDAS = _magnet("c", "[Judas] Tougen Anki - S02E06 [1080p][HEVC x265 10bit][Multi-Subs] (Weekly)")
ERAI_HEVC = _magnet("d", "[Erai-raws] Tougen Anki Nikko Kegon no Taki-hen - 06 [1080p CR WEBRip HEVC AAC][MultiSub][8CCADEFE]")
IDS = {VARYG: "200", ERAI_AVC: "100", JUDAS: "300", ERAI_HEVC: "100"}


def _preparar_rede_falsa():
    pedidos = []

    def get(url, params):
        pedidos.append(url)
        id_nyaa = url.rsplit("/", 1)[1]
        html = f'<div id="torrent-description">{DESCRICOES[id_nyaa]}</div>'
        return SimpleNamespace(text=html)

    at._get_nyaa_com_espelho = get
    at._cache_pt_br_nyaa.clear()
    # Busca RSS: devolve o próprio nome com o id do release.
    nomes = {at._normalizar_titulo_busca(at._nome_da_opcao(m)): (at._nome_da_opcao(m), i) for m, i in IDS.items()}
    at._buscar_nyaa = lambda consulta: [(nomes[consulta][0], f"https://nyaa.si/download/{nomes[consulta][1]}.torrent", 10)] \
        if consulta in nomes else []
    return pedidos


def testar_descricao_do_nyaa():
    pedidos = _preparar_rede_falsa()
    assert at._legenda_pt_br_na_pagina_nyaa("100") is True
    assert at._legenda_pt_br_na_pagina_nyaa("200") is False
    assert at._legenda_pt_br_na_pagina_nyaa("300") is None
    assert at._legenda_pt_br_na_pagina_nyaa("100") is True
    assert len(pedidos) == 3, pedidos  # cache: a descrição não é baixada de novo


def testar_legenda_pela_opcao():
    _preparar_rede_falsa()
    assert at._legenda_pt_br("1080p", VARYG) is False
    assert at._legenda_pt_br("1080p HEVC-2", ERAI_HEVC) is True
    assert at._legenda_pt_br("1080p HEVC", JUDAS) is None
    # Rótulo/nome com PT-BR resolve sem rede; nome fora do nyaa = não dá pra saber.
    assert at._legenda_pt_br("1080p PT-BR", _magnet("e", "[Fansub] Anime - 06")) is True
    assert at._legenda_pt_br("1080p", _magnet("f", "[Fansub] Anime - 06")) is None


def testar_escolha_prefere_pt_br_acima_da_qualidade():
    _preparar_rede_falsa()
    opcoes = [("1080p", VARYG), ("1080p-2", ERAI_AVC), ("1080p HEVC", JUDAS), ("1080p HEVC-2", ERAI_HEVC)]
    assert at._escolher_melhor_magnet(opcoes, 6, avaliar_pt_br=at._legenda_pt_br) == ERAI_HEVC
    # pt-BR confirmado em 1080p ganha de 1080p HEVC sem saber.
    assert at._escolher_melhor_magnet([("1080p HEVC", JUDAS), ("1080p", ERAI_AVC)], 6,
                                      avaliar_pt_br=at._legenda_pt_br) == ERAI_AVC
    # Não saber ganha de confirmado sem.
    assert at._escolher_melhor_magnet([("1080p HEVC", VARYG), ("1080p", JUDAS)], 6,
                                      avaliar_pt_br=at._legenda_pt_br) == JUDAS
    # Sem censura continua acima de tudo.
    sem_censura = _magnet("g", "[WF] Anime - 06 [UNCENSORED]")
    assert at._escolher_melhor_magnet([("1080p", ERAI_AVC), ("720p", sem_censura)], 6,
                                      avaliar_pt_br=at._legenda_pt_br) == sem_censura
    # Sem a função, comportamento antigo (qualidade).
    assert at._escolher_melhor_magnet([("1080p", ERAI_AVC), ("1080p HEVC", JUDAS)], 6) == JUDAS


def testar_nyaa_entra_quando_nada_da_pagina_tem_pt_br():
    _preparar_rede_falsa()
    registro = {"titulo": "Tougen Anki", "url": "x"}
    at._extrair_opcoes_download = lambda url, n: [("1080p", VARYG, None)]
    at._opcoes_nyaa = lambda reg, n: [("nyaa", "https://nyaa.si/download/100.torrent", None)]
    at._obter_torrent = lambda link: (b"", "e" * 40, "")
    total, opcoes = at._opcoes_nao_tentadas(registro, 6)
    assert total == 2 and [o[0] for o in opcoes] == ["1080p", "nyaa"], opcoes
    assert at._escolher_melhor_magnet(opcoes, 6, avaliar_pt_br=at._legenda_pt_br) == "https://nyaa.si/download/100.torrent"
    # Com opção de pt-BR desconhecido na página, o nyaa não é consultado.
    at._extrair_opcoes_download = lambda url, n: [("1080p", VARYG, None), ("1080p HEVC", JUDAS, None)]
    total, opcoes = at._opcoes_nao_tentadas(registro, 6)
    assert total == 2 and [o[0] for o in opcoes] == ["1080p", "1080p HEVC"], opcoes


if __name__ == "__main__":
    testar_descricao_do_nyaa()
    testar_legenda_pela_opcao()
    testar_escolha_prefere_pt_br_acima_da_qualidade()
    testar_nyaa_entra_quando_nada_da_pagina_tem_pt_br()
    print("OK")

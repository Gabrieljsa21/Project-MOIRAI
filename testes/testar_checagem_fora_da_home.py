"""Teste isolado (sem rede) da checagem de lançamentos: encoding sem charset,
consulta à página de animes fora da home e histórico de checagens."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

HOME = """<div class="bixbox latestdark"></div><div class="listupd">
<article class="bs"><div class="bsx"><a href="https://darkmahou.io/anime/na-home/">
<span class="ntitle">Anime 4ª Temporada</span><span class="epsx">Episódio 5</span></a></div></article>
</div>"""

PAGINA_FORA = """<h1>Fora da Home</h1>
<div class="soraddl"><h3>Episódio 10</h3><table><tr><td><a href="magnet:?xt=urn:btih:%s">1080p HEVC</a></td></tr></table></div>
<div class="soraddl"><h3>Episódio 12 Final</h3><table><tr><td><a href="magnet:?xt=urn:btih:%s">1080p HEVC</a></td></tr></table></div>
<div class="soraddl"><h3>Episódio 11</h3><table><tr><td><a href="magnet:?xt=urn:btih:%s">1080p HEVC</a></td></tr></table></div>
""" % ("a" * 40, "c" * 40, "b" * 40)

PAGINAS = {
    at.URL_BASE: HOME,
    "https://darkmahou.io/anime/fora-da-home/": PAGINA_FORA,
    "https://darkmahou.io/anime/na-home/": "<h1>x</h1>",
}


class _RespostaFalsa:
    """Imita o DarkMahou desde 2026-09-23: bytes UTF-8 sem charset no cabeçalho."""

    def __init__(self, url):
        self.content = PAGINAS[url].encode("utf-8")
        self.encoding = "ISO-8859-1"

    def raise_for_status(self):
        pass

    @property
    def text(self):
        return self.content.decode(self.encoding)


def _preparar(animes):
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    at.ARQUIVO_HISTORICO_CHECAGENS = os.path.join(pasta, "historico.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump(animes, f)
    at.requests.get = lambda url, **kw: _RespostaFalsa(url)
    at._buscar_temporada_estreia = lambda url: (False, None)


def testar_html_sem_charset_vira_utf8_e_magnet_e_encontrado():
    _preparar({})
    opcoes = at._extrair_opcoes_download("https://darkmahou.io/anime/fora-da-home/", 12)
    assert opcoes == [("1080p HEVC", "magnet:?xt=urn:btih:" + "c" * 40)], opcoes
    itens = at.listar_ultimos_lancamentos()
    assert itens[0]["titulo"] == "Anime 4ª Temporada", itens


def testar_anime_fora_da_home_e_atualizado_pela_pagina():
    _preparar({
        "fora-da-home": {"titulo": "Fora da Home", "url": "https://darkmahou.io/anime/fora-da-home/",
                         "interesse": "tenho_interesse", "ultimo_episodio_visto": 10,
                         "episodios": {"10": "baixado"}, "downloads_em_andamento": {}},
        "terminado": {"titulo": "Terminado", "url": "https://darkmahou.io/anime/nao-deve-consultar/",
                      "interesse": "tenho_interesse", "ultimo_episodio_visto": 12, "mal_num_episodios": 12,
                      "episodios": {}, "downloads_em_andamento": {}},
        "sem-interesse": {"titulo": "Ignorado", "url": "https://darkmahou.io/anime/nao-deve-consultar/",
                          "interesse": "sem_interesse"},
    })
    relatorio = {}
    at.verificar_novos_lancamentos(relatorio)
    animes = at._carregar_animes()
    assert animes["fora-da-home"]["ultimo_episodio_visto"] == 12
    assert at._episodios_a_baixar(animes["fora-da-home"]) == [11, 12]
    assert relatorio["paginas_consultadas"] == 1 and relatorio["paginas_com_erro"] == []
    assert relatorio["episodios_novos"] == [{"titulo": "Fora da Home", "de": 10, "para": 12, "fonte": "pagina"}]


def testar_checagem_completa_grava_historico():
    _preparar({})
    at.qbittorrent_configurado = lambda: False
    for nome in ("backfill_temporadas_estreia", "casar_animes_com_mal", "casar_animes_com_anilist"):
        setattr(at, nome, lambda: None)
    at.obter_estados_lancamento_anilist = lambda: []
    at.obter_lembretes_atraso = lambda: []
    at.executar_checagem_completa()
    at.executar_checagem_completa()
    historico = at.obter_historico_checagens()
    assert len(historico) == 2
    assert historico[0]["itens_home"] == 1 and historico[0]["erro"] is None
    assert historico[0]["inicio"] >= historico[1]["inicio"]
    assert len(at.obter_historico_checagens(1)) == 1


def testar_episodio_do_meio_que_falhou_continua_sendo_tentado():
    # E17 falhou (sem magnet), E18 baixou: o E17 precisa continuar na lista.
    registro = {"ultimo_episodio_visto": 18, "episodios": {"16": "assistido"},
                "downloads_em_andamento": {"18": {"hash": "x"}}}
    assert at._episodios_a_baixar(registro) == [17]


def testar_episodio_apagado_pelo_usuario_nao_e_baixado_de_novo():
    # sincronizar_biblioteca_local remove "baixado" de `episodios` quando o
    # arquivo some; o registro de auditoria impede o redownload.
    registro = {"ultimo_episodio_visto": 12, "episodios": {"10": "assistido"},
                "episodios_revertido_em": {"11": "apagado"},
                "episodios_download_disparado_em": {"12": "2026-09-24 00:52 (hash)"}}
    assert at._episodios_a_baixar(registro) == []


def testar_acompanhamento_iniciado_no_meio_nao_baixa_do_inicio():
    registro = {"ultimo_episodio_visto": 8, "episodios": {"5": "baixado"}}
    assert at._episodios_a_baixar(registro) == [6, 7, 8]
    assert at._episodios_a_baixar({"ultimo_episodio_visto": 3}) == [1, 2, 3]


def testar_download_disparado_fica_registrado():
    _preparar({"fora-da-home": {"titulo": "Fora da Home", "url": "https://darkmahou.io/anime/fora-da-home/",
                                "interesse": "tenho_interesse", "ultimo_episodio_visto": 12,
                                "episodios": {}, "downloads_em_andamento": {}}})

    class _ClienteFalso:
        def torrents_add(self, **kw):
            pass

    at._cliente_qbittorrent = lambda: _ClienteFalso()
    at.obter_anime_pasta_downloads = lambda: tempfile.mkdtemp()
    registro = at._carregar_animes()["fora-da-home"]
    assert at.baixar_episodio("fora-da-home", registro, 12) is True
    salvo = at._carregar_animes()["fora-da-home"]
    assert "12" in salvo["episodios_download_disparado_em"]
    assert ("c" * 40) in salvo["episodios_download_disparado_em"]["12"]


if __name__ == "__main__":
    testar_html_sem_charset_vira_utf8_e_magnet_e_encontrado()
    testar_anime_fora_da_home_e_atualizado_pela_pagina()
    testar_checagem_completa_grava_historico()
    testar_episodio_do_meio_que_falhou_continua_sendo_tentado()
    testar_episodio_apagado_pelo_usuario_nao_e_baixado_de_novo()
    testar_acompanhamento_iniciado_no_meio_nao_baixa_do_inicio()
    testar_download_disparado_fica_registrado()
    print("OK")

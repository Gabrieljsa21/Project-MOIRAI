"""Teste isolado (sem rede): link do trailer no YouTube lido do botão
"Trailer" da página do anime (`a.trailerbutton`, caso real Kusuriya no
Hitorigoto 3ª Temporada, 2026-10-03) - gravado pelo adicionar manual e lido
sob demanda (com cache) para quem foi rastreado antes do campo existir."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup

from moirai.core import anime_tracker as at

URL = "https://darkmahou.io/anime-3a-temporada/"
TRAILER = "https://www.youtube.com/watch?v=9aFlpf00KZs"
HTML = (f'<h1>Anime 3ª Temporada</h1><a data-fancybox href="{TRAILER}" class="trailerbutton">'
        '<i class="fab fa-youtube"></i> Trailer </a>')
HTML_SEM_TRAILER = '<h1>Anime 3ª Temporada</h1>'


def _preparar(animes):
    at.ARQUIVO_ANIMES = os.path.join(tempfile.mkdtemp(), "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump(animes, f)
    at._cache_trailer.clear()


def testar_trailer_da_pagina():
    assert at._trailer_da_pagina(BeautifulSoup(HTML, "html.parser")) == TRAILER
    assert at._trailer_da_pagina(BeautifulSoup(HTML_SEM_TRAILER, "html.parser")) is None


def testar_adicionar_manual_grava_trailer():
    _preparar({})
    at._obter_html_darkmahou = lambda url: HTML
    chave, erro = at.adicionar_anime_manual(URL)
    assert erro is None and at._carregar_animes()[chave]["trailer_url"] == TRAILER


def testar_obter_trailer_sob_demanda_com_cache():
    _preparar({"anime": {"titulo": "Anime", "url": URL, "interesse": "pendente"}})
    requisicoes = []
    at._obter_html_darkmahou = lambda url: requisicoes.append(url) or HTML
    assert at.obter_trailer("anime") == (TRAILER, None)
    assert at.obter_trailer("anime") == (TRAILER, None)
    assert requisicoes == [URL]  # 2º clique vem do cache


def testar_obter_trailer_sem_trailer_e_inexistente():
    _preparar({"anime": {"titulo": "Anime", "url": URL, "interesse": "pendente"}})
    at._obter_html_darkmahou = lambda url: HTML_SEM_TRAILER
    assert at.obter_trailer("anime") == (None, None)
    assert at.obter_trailer("outro")[0] is None and at.obter_trailer("outro")[1]


if __name__ == "__main__":
    testar_trailer_da_pagina()
    testar_adicionar_manual_grava_trailer()
    testar_obter_trailer_sob_demanda_com_cache()
    testar_obter_trailer_sem_trailer_e_inexistente()
    print("OK")

"""Teste isolado (sem rede) da home do DarkMahou no layout de 2026-10-07
(`section.dm-latest` / `article.dm-card`), que substituiu a `div.latestdark`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

HOME_DM = """<section class="dm-latest"><header><h2>Últimos lançamentos</h2></header><div class="dm-grid">
<article class="dm-card"><a href="https://darkmahou.io/anime-x-4a-temporada/"><div class="dm-poster">
<img src="https://darkmahou.io/capa.jpg" /><span class="dm-score">★ 9.18</span><span class="dm-episode">EP 19</span></div>
<h3>Anime X 4ª Temporada</h3><p>Concluído</p></a></article>
<article class="dm-card"><a href="https://darkmahou.io/anime-y/"><div class="dm-poster"></div><h3>Anime Y</h3><p>Em breve</p></a></article>
</div></section>"""


def _home(html):
    at._cache_html.clear()
    at._obter_html_darkmahou = lambda url: html


def testar_layout_dm_lido():
    _home(HOME_DM)
    itens = at.listar_ultimos_lancamentos()
    assert itens == [
        {"titulo": "Anime X 4ª Temporada", "episodio": 19, "url": "https://darkmahou.io/anime-x-4a-temporada/",
         "capa_url": "https://darkmahou.io/capa.jpg"},
        {"titulo": "Anime Y", "episodio": None, "url": "https://darkmahou.io/anime-y/", "capa_url": None},
    ], itens


def testar_layout_antigo_continua_como_reserva():
    _home("""<div class="bixbox latestdark"></div><div class="listupd">
<article class="bs"><div class="bsx"><a href="https://darkmahou.io/z/"><span class="ntitle">Z</span>
<span class="epsx">Episódio 5</span></a></div></article></div>""")
    assert at.listar_ultimos_lancamentos() == [
        {"titulo": "Z", "episodio": 5, "url": "https://darkmahou.io/z/", "capa_url": None}]


def testar_sem_nenhum_layout_devolve_vazio():
    _home("<html><body>nada</body></html>")
    assert at.listar_ultimos_lancamentos() == []


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("testar_") and callable(f):
            f()
            print("OK", nome)

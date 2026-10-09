"""Teste isolado (sem rede) da página do anime no layout de 2026-10-09
(`details.dm-download-group`), que substituiu os blocos `div.soraddl`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at

PAGINA_DM = """<section id="dm-downloads"><div class="dm-download-box"><div class="dm-download-list">
<details class="dm-download-group"><summary><span class="dm-download-icon">↓</span>
<span class="dm-download-title">Episódio 01</span></summary><div class="dm-download-resolutions">
<div class="dm-resolution"><span class="dm-quality">Baixar&gt;&gt;</span><div class="dm-hosts">
<a href="magnet:?xt=urn:btih:1111111111111111111111111111111111111111&amp;dn=Ep01"><span>1080p</span></a>
<a href="magnet:?xt=urn:btih:2222222222222222222222222222222222222222&amp;dn=Ep01HEVC"><span>1080p HEVC</span><span aria-hidden="true">↗</span><span class="screen-reader-text"> (abre em nova aba)</span></a>
</div></div>
<div class="dm-resolution"><span class="dm-quality">Dublado.&gt;&gt;</span><div class="dm-hosts">
<a href="magnet:?xt=urn:btih:3333333333333333333333333333333333333333&amp;dn=Ep01Dub"><span>1080p</span></a>
</div></div></div></details>
<details class="dm-download-group"><summary><span class="dm-download-title">Episódio 02</span></summary>
<div class="dm-download-resolutions"><div class="dm-resolution"><span class="dm-quality">Baixar&gt;&gt;</span>
<div class="dm-hosts"><a href="magnet:?xt=urn:btih:4444444444444444444444444444444444444444&amp;dn=Ep02"><span>1080p</span></a>
</div></div></div></details>
</div></div></section>"""


class _RespostaFalsa:
    def __init__(self, texto):
        self.text, self.encoding = texto, None

    def raise_for_status(self):
        pass


def _pagina(html):
    at._cache_html.clear()
    at.requests.get = lambda *a, **k: _RespostaFalsa(html)


def testar_episodio_le_so_a_linha_legendada():
    _pagina(PAGINA_DM)
    opcoes = at._extrair_opcoes_download("https://darkmahou.io/x/", 1)
    assert [rotulo for rotulo, _, _ in opcoes] == ["1080p", "1080p HEVC"], opcoes
    assert all("Dub" not in link for _, link, _ in opcoes), opcoes


def testar_maior_episodio_e_hashes_por_episodio():
    _pagina(PAGINA_DM)
    assert at._extrair_opcoes_download("https://darkmahou.io/x/", 2)[0][0] == "1080p"
    hashes = at._extrair_hashes_por_episodio("https://darkmahou.io/x/")
    assert sorted(hashes) == [1, 2], hashes


def testar_layout_antigo_passa_intacto():
    html = "<div class=\"soraddl\"><h3>Episódio 01</h3><table><tr><td></td></tr></table></div>"
    assert at._normalizar_blocos_download(html) == html


if __name__ == "__main__":
    for nome, f in list(globals().items()):
        if nome.startswith("testar_") and callable(f):
            f()
            print("OK", nome)

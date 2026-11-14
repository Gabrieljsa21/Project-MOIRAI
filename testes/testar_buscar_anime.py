"""Teste (sem rede): busca de anime pelo nome no DarkMahou (2026-11-13,
pedido do usuário: "moirai podia pesquisar os animes p baixar em vez de
pegar apenas o link direto")."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at


def _card(slug, titulo, ep, status, formato="TV"):
    episodio = f'<span class="dm-episode">EP {ep}</span>' if ep else ""
    return (f'<article class="dm-card"><a href="https://darkmahou.io/{slug}/"><div class="dm-poster">'
            f'<img alt="{titulo}" src="https://darkmahou.io/capa-{slug}.webp"/>'
            f'<span class="dm-score">★ 7.43</span>{episodio}<span class="dm-format">{formato}</span>'
            f'</div><h3>{titulo}</h3><p>{status}</p></a></article>')


HTML = ("<main>" + _card("chitose-kun-wa-ramune-bin-no-naka-part-2", "Chitose-kun wa Ramune Bin no Naka Part 2", 5, "Em lançamento")
        + _card("chitose-kun-wa-ramune-bin-no-naka", "Chitose-kun wa Ramune Bin no Naka", 13, "Concluído")
        + _card("rakudai-majo", "Rakudai Majo: Fuuka to Yami no Majo", None, "Concluído", "Filme")
        + '<article class="dm-card"><a href="https://outro-site.com/x/"><h3>Anúncio</h3></a></article></main>')


def testar_busca_por_nome():
    at.ARQUIVO_ANIMES = os.path.join(tempfile.mkdtemp(), "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump({"chitose-kun-wa-ramune-bin-no-naka-part-2": {"titulo": "x", "interesse": "tenho_interesse"},
                   "rakudai-majo": {"titulo": "y", "interesse": "sem_interesse"}}, f)
    pedidos = []
    at._obter_html_darkmahou = lambda url: pedidos.append(url) or HTML

    resultados, erro = at.buscar_animes_no_site("  Ramune   bin  ")
    assert erro is None and pedidos == ["https://darkmahou.io/?s=Ramune+bin"], pedidos
    assert [r["titulo"] for r in resultados] == ["Chitose-kun wa Ramune Bin no Naka Part 2",
                                                 "Chitose-kun wa Ramune Bin no Naka",
                                                 "Rakudai Majo: Fuuka to Yami no Majo"]
    primeiro, segundo, filme = resultados
    assert primeiro["ja_rastreado"] is True and segundo["ja_rastreado"] is False
    assert filme["ja_rastreado"] is False  # "sem interesse" pode ser adicionado de novo
    assert segundo == {"titulo": "Chitose-kun wa Ramune Bin no Naka",
                       "url": "https://darkmahou.io/chitose-kun-wa-ramune-bin-no-naka/",
                       "capa_url": "https://darkmahou.io/capa-chitose-kun-wa-ramune-bin-no-naka.webp",
                       "episodios": 13, "status": "Concluído", "formato": "TV", "ja_rastreado": False}
    assert filme["episodios"] is None and filme["formato"] == "Filme"

    assert at.buscar_animes_no_site("   ") == ([], "Digite o nome do anime.")
    assert len(pedidos) == 1

    def falha(url):
        raise OSError("sem rede")
    at._obter_html_darkmahou = falha
    resultados, erro = at.buscar_animes_no_site("chitose")
    assert resultados == [] and "sem rede" in erro


if __name__ == "__main__":
    testar_busca_por_nome()
    print("OK")

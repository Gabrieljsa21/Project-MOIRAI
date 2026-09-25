"""Teste isolado: só "Episódio N" conta como número de episódio. Casos reais
de 2026-09-24: título do anime com número ("100-nin no Kanojo" virava 100
episódios), pacote de temporada ("2ª Temporada BD Completo" virava 2) e
erro de digitação do site ("Epi8ódio 18" nunca era encontrado)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup

from moirai.core import anime_tracker as at

at._buscar_nyaa = lambda consulta: []  # sem rede: reserva do nyaa desligada nos testes


def testar_numero_do_bloco():
    casos = {
        "Episódio 12 Final": 12, "Epi8ódio 18": 18, "Episodio 3": 3, "Episódio 07": 7,
        "Episódios 01~04 Sem Censura": None, "Episódio Especial": None, "Episódio 00": None, "Filme Completo Legendado Torrent": 1,
        "Kimi no Koto ga Daidaidaidaidaisuki na 100-nin no Kanojo": None,
        "2ª Temporada BD Completo Legendado Torrent": None,
    }
    for texto, esperado in casos.items():
        assert at._numero_do_bloco(texto) == esperado, (texto, at._numero_do_bloco(texto))


def testar_ultimo_episodio_ignora_titulo_do_anime():
    html = "".join(f'<div class="soraddl"><h3>{t}</h3></div>' for t in (
        "Kimi no Koto ga Daidaidaidaidaisuki na 100-nin no Kanojo", "Episódio 02", "Epi8ódio 03"))
    assert at._ultimo_episodio_da_pagina(BeautifulSoup(html, "html.parser")) == 3
    so_pacote = '<div class="soraddl"><h3>2ª Temporada BD Completo Legendado Torrent</h3></div>'
    assert at._ultimo_episodio_da_pagina(BeautifulSoup(so_pacote, "html.parser")) is None


if __name__ == "__main__":
    testar_numero_do_bloco()
    testar_ultimo_episodio_ignora_titulo_do_anime()
    print("OK")

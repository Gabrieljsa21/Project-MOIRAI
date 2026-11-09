"""Testes (sem rede, sem qBittorrent, sem Lixeira real) das melhorias de
2026-11-09: backup diário do registro, nomes sem diferenciar maiúsculas,
episódio corrompido baixado de novo e episódio sem pt-BR trocado."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at


def _preparar(animes):
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump(animes, f)
    at._titulos_canonicos.update(mtime=None, mapa={})
    at._episodios_corrompidos.clear()
    at._episodios_sem_pt_br.clear()
    at.qbittorrent_configurado = lambda: True
    return pasta


def testar_backup_diario_e_gravacao_atomica():
    pasta = _preparar({"a": {"titulo": "Anime A"}})
    try:
        backups = os.path.join(pasta, "backups")
        # Backups antigos: só os 7 mais recentes ficam.
        os.makedirs(backups)
        for dia in range(1, 10):
            open(os.path.join(backups, f"animes.2026-01-0{dia}.json"), "w").write("{}")
        at._salvar_animes({"a": {"titulo": "Anime A", "x": 1}})
        nomes = sorted(os.listdir(backups))
        assert len(nomes) == 7 and nomes[0] == "animes.2026-01-04.json", nomes
        # O backup de hoje guarda o estado ANTERIOR à 1ª gravação do dia.
        assert "x" not in json.load(open(os.path.join(backups, nomes[-1]), encoding="utf-8"))["a"]
        at._salvar_animes({"a": {"titulo": "Anime A", "x": 2}})
        assert sorted(os.listdir(backups)) == nomes  # 2ª gravação do dia não cria outro
        assert at._carregar_animes()["a"]["x"] == 2
        assert not os.path.exists(at.ARQUIVO_ANIMES + ".tmp")
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def testar_nome_em_minusculas_continua_reconhecido():
    pasta = _preparar({"a": {"titulo": "Anime A: Parte 2"}})
    try:
        for nome in ("anime a parte 2 - s01e03.mkv", "Anime A Parte 2 - S01E04.mkv", "outro - s01e01.mkv"):
            open(os.path.join(pasta, nome), "wb").write(b"x")
        mapa = at._mapear_arquivos_por_titulo(pasta)
        assert mapa == {"Anime A Parte 2": {3, 4}, "outro": {1}}, mapa
        assert at._titulo_do_nome_arquivo("ANIME A PARTE 2 - S01E05.mkv") == "Anime A Parte 2"
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def testar_corrompido_vai_para_lixeira_e_baixa_de_novo():
    animes = {"a": {"titulo": "Anime A", "interesse": "tenho_interesse", "url": "x",
                    "episodios": {"1": "baixado", "2": "assistido"},
                    "episodios_na_pasta_do_anime": ["1", "2"]}}
    pasta = _preparar(animes)
    try:
        ruim = os.path.join(pasta, "Anime A - S01E01.mkv")
        visto = os.path.join(pasta, "Anime A - S01E02.mkv")
        for c in (ruim, visto):
            open(c, "wb").write(b"\0" * 8)
        lixeira, baixados = [], []
        at._mandar_para_lixeira = lambda c: (lixeira.append(os.path.basename(c)), os.remove(c))
        at.baixar_episodio = lambda chave, reg, n, **k: baixados.append((chave, n, k)) or True
        at._episodios_corrompidos.update({os.path.normcase(ruim): ruim, os.path.normcase(visto): visto})

        assert at.tratar_episodios_com_problema() == 1
        assert lixeira == ["Anime A - S01E01.mkv"] and baixados == [("a", 1, {})]
        registro = at._carregar_animes()["a"]
        # Fora de "baixado" e da pasta do anime: a varredura não marca como assistido.
        assert registro["episodios"] == {"2": "assistido"} and registro["episodios_na_pasta_do_anime"] == ["2"]
        assert "1" in registro["episodios_rebaixado_corrompido"] and os.path.exists(visto)

        # Veio corrompido de novo: não baixa em círculos, vira falha registrada.
        registro["episodios"]["1"] = "baixado"
        at._salvar_animes({"a": registro})
        open(ruim, "wb").write(b"\0" * 8)
        falhas = []
        at._registrar_falha_download = lambda chave, n, motivo: falhas.append((chave, n))
        at._episodios_corrompidos[os.path.normcase(ruim)] = ruim
        assert at.tratar_episodios_com_problema() == 0
        assert falhas == [("a", 1)] and len(baixados) == 1 and os.path.exists(ruim)
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def testar_sem_pt_br_procura_release_uma_vez_por_dia():
    animes = {"a": {"titulo": "Anime A", "interesse": "tenho_interesse", "url": "x",
                    "episodios": {"6": "baixado", "7": "assistido"}}}
    pasta = _preparar(animes)
    try:
        sem_pt = os.path.join(pasta, "Anime A - S01E06.mkv")
        visto = os.path.join(pasta, "Anime A - S01E07.mkv")
        for c in (sem_pt, visto):
            open(c, "wb").write(b"x")
        pedidos = []
        at.baixar_episodio = lambda chave, reg, n, **k: pedidos.append((n, k.get("substituir"))) or True
        at._episodios_sem_pt_br.update({os.path.normcase(sem_pt): sem_pt, os.path.normcase(visto): visto})

        assert at.tratar_episodios_com_problema() == 1
        assert pedidos == [(6, sem_pt)]  # o já assistido não é trocado
        assert os.path.exists(sem_pt)  # o antigo só sai quando o novo termina
        assert at.tratar_episodios_com_problema() == 0 and len(pedidos) == 1  # mesmo dia: não repete
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def testar_baixar_episodio_substituir_exige_pt_br_e_release_diferente():
    magnet = lambda letra: f"magnet:?xt=urn:btih:{letra * 40}&dn=x"
    registro = {"titulo": "Anime A", "url": "x", "episodios": {"6": "baixado"},
                "episodios_download_disparado_em": {"6": f"2026-11-07 10:00 ({'a' * 40})"}}
    pasta = _preparar({"a": registro})
    try:
        pt_br = {magnet("a"): True, magnet("b"): False, magnet("c"): None, magnet("d"): True}
        at._legenda_pt_br = lambda rotulo, link: pt_br[link]
        adicionados = []
        at._cliente_qbittorrent = lambda: object()
        at._adicionar_torrent = lambda cliente, link, pasta_destino, parar_no_metadado=False: adicionados.append(link)
        at.obter_anime_pasta_downloads = lambda: pasta

        # Só o release atual (a) tem pt-BR: nada a trocar, e não registra falha.
        at._opcoes_nao_tentadas = lambda reg, n: (3, [("1080p", magnet("a"), None), ("1080p", magnet("b"), None),
                                                      ("1080p HEVC", magnet("c"), None)])
        assert at.baixar_episodio("a", registro, 6, substituir="antigo.mkv") is False and adicionados == []
        assert "episodios_falha_download" not in at._carregar_animes()["a"]

        at._opcoes_nao_tentadas = lambda reg, n: (2, [("1080p HEVC", magnet("c"), None), ("1080p", magnet("d"), None)])
        assert at.baixar_episodio("a", registro, 6, substituir="antigo.mkv") is True
        assert adicionados == [magnet("d")]
        info = at._carregar_animes()["a"]["downloads_em_andamento"]["6"]
        assert info["substituir"] == "antigo.mkv" and info["hash"] == "d" * 40
        # Sem `substituir`, episódio baixado continua não sendo baixado de novo.
        assert at.baixar_episodio("a", registro, 6) is False
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def testar_varredura_anota_corrompido_e_sem_pt_br():
    pasta = _preparar({})
    try:
        nomes = ["Anime A - S01E01.mkv", "Anime B - S01E01.mkv", "Anime C - S01E01.mkv", "Anime D - S01E01.mkv"]
        for n in nomes:
            open(os.path.join(pasta, n), "wb").write(b"x")
        faixa = lambda lang: {"lang": lang, "title": "", "default": False}

        def faixas(c):
            nome = os.path.basename(c)
            if nome == nomes[0]:
                raise RuntimeError("EBML header parsing failed: Invalid data found when processing input")
            return {nomes[1]: [faixa("eng"), faixa("spa")],   # multi-legendas sem português
                    nomes[2]: [faixa("und")],                 # fansub: faixa única sem idioma
                    nomes[3]: []}[nome]                       # legenda na imagem

        at.obter_anime_pasta_downloads = lambda: pasta
        at.obter_anime_pasta_assistidos = lambda: ""
        at._arquivos_em_torrents_ativos = lambda: set()
        at._faixas_legenda = faixas
        at._legendas_conferidas.clear()
        assert at.padronizar_legendas_pt_br() == []
        assert [os.path.basename(c) for c in at._episodios_corrompidos.values()] == [nomes[0]]
        assert [os.path.basename(c) for c in at._episodios_sem_pt_br.values()] == [nomes[1]]
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


if __name__ == "__main__":
    testar_backup_diario_e_gravacao_atomica()
    testar_nome_em_minusculas_continua_reconhecido()
    testar_varredura_anota_corrompido_e_sem_pt_br()
    testar_baixar_episodio_substituir_exige_pt_br_e_release_diferente()
    testar_corrompido_vai_para_lixeira_e_baixa_de_novo()
    testar_sem_pt_br_procura_release_uma_vez_por_dia()
    print("OK")

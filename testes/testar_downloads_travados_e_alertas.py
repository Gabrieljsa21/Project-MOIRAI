"""Teste isolado (sem rede, sem qBittorrent real): download travado troca de
magnet, numeração absoluta aceita quando o arquivo pertence ao torrent,
alertas de falha persistente/site, entrega de checagem autônoma e rotação de
logs."""
import datetime
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai import runtime_log
from moirai.core import anime_tracker as at

HASH_A, HASH_B = "a" * 40, "b" * 40
URL = "https://darkmahou.io/anime/teste/"


def _preparar(animes):
    pasta = tempfile.mkdtemp()
    at.ARQUIVO_ANIMES = os.path.join(pasta, "animes.json")
    at.ARQUIVO_HISTORICO_CHECAGENS = os.path.join(pasta, "historico.json")
    at.ARQUIVO_RESULTADOS_NAO_ENTREGUES = os.path.join(pasta, "nao_entregues.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump(animes, f)
    at.qbittorrent_configurado = lambda: True
    at.obter_anime_pasta_downloads = lambda: pasta
    return pasta


class _ClienteFalso:
    def __init__(self, torrents=None, arquivos=None):
        self.torrents = torrents or {}
        self.arquivos = arquivos or {}
        self.adicionados = []
        self.removidos = []

    def torrents_info(self, torrent_hashes):
        return [self.torrents[torrent_hashes]] if torrent_hashes in self.torrents else []

    def torrents_files(self, torrent_hash):
        return [SimpleNamespace(name=n) for n in self.arquivos.get(torrent_hash, [])]

    def torrents_add(self, urls, **kw):
        self.adicionados.append(at._hash_do_magnet(urls))

    def torrents_delete(self, delete_files, torrent_hashes):
        assert delete_files is False
        self.removidos.append(torrent_hashes)


def _registro(**extra):
    base = {"titulo": "Teste", "url": URL, "interesse": "tenho_interesse", "ultimo_episodio_visto": 8,
            "episodios": {"7": "assistido"}, "downloads_em_andamento": {}}
    base.update(extra)
    return base


def testar_download_travado_troca_para_o_proximo_magnet():
    antigo = (datetime.datetime.now() - datetime.timedelta(hours=30)).strftime("%Y-%m-%d %H:%M")
    _preparar({"teste": _registro(downloads_em_andamento={
        "8": {"hash": HASH_A, "pasta": "x", "progresso": 0.3, "progresso_em": antigo}})})
    cliente = _ClienteFalso(torrents={HASH_A: SimpleNamespace(progress=0.3, content_path="")})
    at._cliente_qbittorrent = lambda: cliente
    at._extrair_opcoes_download = lambda url, n: [
        ("1080p HEVC", f"magnet:?xt=urn:btih:{HASH_A}"), ("1080p", f"magnet:?xt=urn:btih:{HASH_B}")]
    at.verificar_downloads_em_andamento()
    salvo = at._carregar_animes()["teste"]
    assert cliente.removidos == [HASH_A]
    assert cliente.adicionados == [HASH_B], cliente.adicionados
    assert salvo["downloads_em_andamento"]["8"]["hash"] == HASH_B
    assert salvo["episodios_magnets_tentados"]["8"] == [HASH_A]


def testar_travado_sem_alternativa_registra_falha():
    antigo = (datetime.datetime.now() - datetime.timedelta(hours=30)).strftime("%Y-%m-%d %H:%M")
    _preparar({"teste": _registro(downloads_em_andamento={
        "8": {"hash": HASH_A, "pasta": "x", "progresso": 0.0, "progresso_em": antigo}})})
    cliente = _ClienteFalso()  # torrent sumiu do qBittorrent
    at._cliente_qbittorrent = lambda: cliente
    at._extrair_opcoes_download = lambda url, n: [("1080p HEVC", f"magnet:?xt=urn:btih:{HASH_A}")]
    at.verificar_downloads_em_andamento()
    salvo = at._carregar_animes()["teste"]
    assert salvo["downloads_em_andamento"] == {}
    assert salvo["episodios_falha_download"]["8"]["motivo"] == "todos os magnets disponíveis já travaram"


def testar_progresso_renova_o_prazo():
    antigo = (datetime.datetime.now() - datetime.timedelta(hours=30)).strftime("%Y-%m-%d %H:%M")
    _preparar({"teste": _registro(downloads_em_andamento={
        "8": {"hash": HASH_A, "pasta": "x", "progresso": 0.3, "progresso_em": antigo}})})
    cliente = _ClienteFalso(torrents={HASH_A: SimpleNamespace(progress=0.5, content_path="")})
    at._cliente_qbittorrent = lambda: cliente
    at.verificar_downloads_em_andamento()
    info = at._carregar_animes()["teste"]["downloads_em_andamento"]["8"]
    assert cliente.removidos == [] and info["progresso"] == 0.5 and info["progresso_em"] != antigo


def testar_numeracao_absoluta_aceita_quando_arquivo_pertence_ao_torrent():
    pasta = _preparar({"teste": _registro(downloads_em_andamento={"8": {"hash": HASH_A, "pasta": "x"}})})
    arquivo = os.path.join(pasta, "[Judas] Teste - S17E48.mkv")
    open(arquivo, "w").close()
    cliente = _ClienteFalso(torrents={HASH_A: SimpleNamespace(progress=1.0, content_path=arquivo)},
                            arquivos={HASH_A: ["[Judas] Teste - S17E48.mkv"]})
    at._cliente_qbittorrent = lambda: cliente
    assert at.verificar_downloads_em_andamento() == 1
    salvo = at._carregar_animes()["teste"]
    assert salvo["episodios"]["8"] == "baixado"
    assert "8" in salvo["episodios_numeracao_divergente_aceita"]
    assert os.path.exists(os.path.join(pasta, "Teste - S01E08.mkv"))


def testar_divergencia_fora_do_torrent_continua_travada_e_avisa_uma_vez(capsys=None):
    pasta = _preparar({"teste": _registro(downloads_em_andamento={"8": {"hash": HASH_A, "pasta": "x"}})})
    arquivo = os.path.join(pasta, "Outro - S01E20.mkv")
    open(arquivo, "w").close()
    cliente = _ClienteFalso(torrents={HASH_A: SimpleNamespace(progress=1.0, content_path=arquivo)},
                            arquivos={HASH_A: ["Teste - S01E08.mkv"]})
    at._cliente_qbittorrent = lambda: cliente
    at.verificar_downloads_em_andamento()
    primeiro = at._carregar_animes()["teste"]["episodios_erro_renomear"]["8"]
    at.verificar_downloads_em_andamento()
    salvo = at._carregar_animes()["teste"]
    assert "8" in salvo["downloads_em_andamento"] and "8" not in salvo["episodios"]
    assert salvo["episodios_erro_renomear"]["8"] == primeiro  # não regravou nem repetiu o aviso


def testar_alerta_de_falha_persistente_sai_uma_vez_so():
    antigo = (datetime.datetime.now() - datetime.timedelta(hours=50)).strftime("%Y-%m-%d %H:%M")
    recente = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    _preparar({"teste": _registro(episodios_falha_download={
        "8": {"desde": antigo, "motivo": "nenhum magnet na página do anime", "alertado": False},
        "9": {"desde": recente, "motivo": "x", "alertado": False}})})
    alertas = at._coletar_alertas_falha_persistente()
    assert len(alertas) == 1 and "Episódio 8 falha há 50h" in alertas[0], alertas
    assert at._coletar_alertas_falha_persistente() == []


def testar_alertas_de_site():
    assert len(at._alertas_de_site({"itens_home": 0}, [], [])) == 1
    assert len(at._alertas_de_site({"itens_home": 20}, [], [("A", 1), ("B", 2)])) == 1
    assert at._alertas_de_site({"itens_home": 20}, [("A", 1)], [("B", 2), ("C", 3)]) == []


def testar_resultado_da_checagem_autonoma_e_entregue_na_proxima():
    _preparar({})
    at._guardar_resultado_nao_entregue(datetime.datetime(2026, 9, 20, 10, 0),
                                       {"texto_download_iniciado": "⬇ Começou a baixar:\n- A - Episódio 3"})
    resultado = {"texto_download_iniciado": "⬇ Começou a baixar:\n- B - Episódio 1", "texto_alertas": None}
    at._incluir_resultados_nao_entregues(resultado)
    assert resultado["texto_download_iniciado"].startswith("(checagem de 20/09 10:00, com a GAIA fechada)")
    assert "B - Episódio 1" in resultado["texto_download_iniciado"]
    assert resultado["texto_alertas"] is None
    assert at._carregar_resultados_nao_entregues() == []


def testar_rotacao_de_logs():
    pasta = tempfile.mkdtemp()
    for nome in ("2026-08-01.log", "2026-09-20.log", "anotacoes.log", "outro.txt"):
        open(os.path.join(pasta, nome), "w").close()
    runtime_log.remover_logs_antigos(pasta, dias=30, hoje=datetime.date(2026, 9, 24))
    assert sorted(os.listdir(pasta)) == ["2026-09-20.log", "anotacoes.log", "outro.txt"]


def testar_desempate_prefere_numeracao_do_episodio_sem_trocar_qualidade():
    # Caso real (Yomi no Tsugai, bloco "Episódio 23"): Judas numera 2 atrás.
    judas = f"magnet:?xt=urn:btih:{HASH_A}&dn=%5BJudas%5D%20Yomi%20no%20Tsugai%20-%20S01E21%20%5B1080p%5D"
    dkb = f"magnet:?xt=urn:btih:{HASH_B}&dn=%5BDKB%5D%20Yomi%20no%20Tsugai%20-%20S01E23%20%5B1080p%5D"
    erai = "magnet:?xt=urn:btih:" + "c" * 40 + "&dn=%5BErai-raws%5D%20Yomi%20no%20Tsugai%20-%2023%20%5B1080p%5D"
    opcoes = [("1080p HEVC", judas), ("1080p HEVC-2", dkb), ("1080p", erai)]
    assert at._escolher_melhor_magnet(opcoes, 23) == dkb
    assert at._escolher_melhor_magnet(opcoes) == judas  # sem número: comportamento antigo
    # Qualidade continua mandando: só o 1080p (sem HEVC) tem o número certo.
    assert at._escolher_melhor_magnet([("1080p HEVC", judas), ("1080p", erai)], 23) == judas


def testar_loop_de_downloads_usa_intervalo_configurado():
    from moirai import main

    class _Parar(Exception):
        pass

    chamadas, esperas = [], []
    at.verificar_downloads_em_andamento = lambda: chamadas.append(at.lock_estado_animes._is_owned())
    main.config.obter_anime_tracker_ativo = lambda: True
    main.time.sleep = lambda s: (esperas.append(s), (_ for _ in ()).throw(_Parar()))
    for configurado, esperado in ((30, 30), (1, 5)):
        main.config.obter_anime_intervalo_downloads_segundos = lambda c=configurado: c
        try:
            main._loop_downloads()
        except _Parar:
            pass
        assert esperas[-1] == esperado, esperas
    assert chamadas == [True, True]  # sempre dentro do lock de estado


def testar_checagem_autonoma_so_roda_com_gaia_fechada_e_intervalo_vencido():
    from moirai import main
    chamadas = []
    at.executar_checagem_completa = lambda origem="gaia": chamadas.append(origem)
    main.config.obter_anime_checagem_autonoma_ativa = lambda: True
    main.config.obter_anime_checagem_autonoma_intervalo_horas = lambda: 6
    cenarios = [(True, None, 0), (False, 2.0, 0), (False, 7.0, 1), (False, None, 1)]
    for gaia_de_pe, horas, esperado in cenarios:
        chamadas.clear()
        main._gaia_rodando = lambda g=gaia_de_pe: g
        at.horas_desde_ultima_checagem = lambda h=horas: h
        main._checagem_autonoma_se_preciso()
        assert len(chamadas) == esperado, (gaia_de_pe, horas, chamadas)
    assert chamadas == ["autonoma"]


if __name__ == "__main__":
    testar_download_travado_troca_para_o_proximo_magnet()
    testar_travado_sem_alternativa_registra_falha()
    testar_progresso_renova_o_prazo()
    testar_numeracao_absoluta_aceita_quando_arquivo_pertence_ao_torrent()
    testar_divergencia_fora_do_torrent_continua_travada_e_avisa_uma_vez()
    testar_alerta_de_falha_persistente_sai_uma_vez_so()
    testar_alertas_de_site()
    testar_resultado_da_checagem_autonoma_e_entregue_na_proxima()
    testar_rotacao_de_logs()
    testar_loop_de_downloads_usa_intervalo_configurado()
    testar_desempate_prefere_numeracao_do_episodio_sem_trocar_qualidade()
    testar_checagem_autonoma_so_roda_com_gaia_fechada_e_intervalo_vencido()
    print("OK")

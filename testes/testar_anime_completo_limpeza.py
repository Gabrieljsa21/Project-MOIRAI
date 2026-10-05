"""Teste isolado (sem rede, sem MAL de verdade): quando o MAL confirma o último
episódio, os vídeos do anime saem da pasta de assistidos, o aviso de "anime
completo" dispara, e a varredura seguinte da biblioteca não reverte nem pede
de novo os episódios apagados. Pasta de temporada e outro anime ficam intactos."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at


def _preparar(animes):
    base = tempfile.mkdtemp()
    downloads = os.path.join(base, "Downloads")
    assistidos = os.path.join(downloads, "Anime")
    os.makedirs(os.path.join(assistidos, "2026 3-Verão", "Anime Novo"))
    at.ARQUIVO_ANIMES = os.path.join(base, "animes.json")
    with open(at.ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump(animes, f)
    at.obter_anime_pasta_downloads = lambda: downloads
    at.obter_anime_pasta_assistidos = lambda: assistidos
    at.mal_client.esta_configurado = lambda: True
    at.obter_mal_sync_ativo = lambda: True
    return assistidos


def _criar(caminho):
    with open(caminho, "w") as f:
        f.write("x")


def _registro(titulo, total, assistido_ate):
    return {
        "titulo": titulo, "interesse": "tenho_interesse", "mal_anime_id": 1,
        "mal_num_episodios": total, "ultimo_episodio_visto": total,
        "episodios": {str(n): "assistido" for n in range(1, assistido_ate + 1)},
    }


def testar_conclusao_apaga_so_o_anime_concluido():
    assistidos = _preparar({
        "fim": _registro("Anime Fim", 2, 2),
        "meio": _registro("Anime Meio", 12, 3),
    })
    for nome in ("Anime Fim - S01E01.mkv", "Anime Fim - S01E02.mkv", "Anime Meio - S01E03.mkv", "notas.txt"):
        _criar(os.path.join(assistidos, nome))
    temporada = os.path.join(assistidos, "2026 3-Verão", "Anime Novo", "Anime Fim - S01E01.mkv")
    _criar(temporada)

    chamadas_mal = []
    at.mal_client.atualizar_progresso = lambda anime_id, ep, status: (chamadas_mal.append((ep, status)) or (True, None))
    avisos = []
    at.definir_callback_anime_completo(lambda titulo, ep, removidos: avisos.append((titulo, ep, removidos)))

    at.sincronizar_progresso_mal()

    restantes = sorted(os.listdir(assistidos))
    assert "Anime Fim - S01E01.mkv" not in restantes and "Anime Fim - S01E02.mkv" not in restantes, restantes
    assert "Anime Meio - S01E03.mkv" in restantes, "anime em andamento não pode ser apagado"
    assert "notas.txt" in restantes, "arquivo que não é vídeo do anime não pode ser apagado"
    assert os.path.exists(temporada), "pasta de temporada conta como baixado, fica de fora"
    assert sorted(chamadas_mal) == [(2, "completed"), (3, "watching")], chamadas_mal
    assert avisos == [("Anime Fim", 2, 2)], avisos

    registro = at._carregar_animes()["fim"]
    assert registro["episodios_removidos_apos_completo"] == 2
    assert registro["anime_completo_em"]

    at.sincronizar_biblioteca_local()
    episodios = at._carregar_animes()["fim"]["episodios"]
    assert episodios == {"1": "assistido", "2": "assistido"}, f"varredura não pode reverter assistido apagado: {episodios}"

    chamadas_mal.clear()
    avisos.clear()
    at.sincronizar_progresso_mal()
    assert chamadas_mal == [] and avisos == [], "conclusão já sincronizada não repete MAL nem aviso"


def testar_falha_no_mal_nao_apaga():
    assistidos = _preparar({"fim": _registro("Anime Fim", 1, 1)})
    _criar(os.path.join(assistidos, "Anime Fim - S01E01.mkv"))
    at.mal_client.atualizar_progresso = lambda anime_id, ep, status: (False, "erro de teste")
    at.definir_callback_anime_completo(lambda *a: (_ for _ in ()).throw(AssertionError("não devia avisar")))

    at.sincronizar_progresso_mal()

    assert os.listdir(assistidos).count("Anime Fim - S01E01.mkv") == 1


def testar_total_desconhecido_no_casamento_completa_depois():
    """Caso real (Katainaka no Ossan II): casado em exibição, sem total; os 12
    episódios já foram enviados como "watching". Quando o MAL passa a dar o
    total, o anime vira completo. Anime antigo, já completo antes da limpeza
    existir (total conhecido, sem `anime_completo_em`), não é tocado."""
    tardio = _registro("Anime Tardio", None, 2)
    tardio["mal_ultimo_progresso_sincronizado"] = 2
    antigo = _registro("Anime Antigo", 2, 2)
    antigo["mal_ultimo_progresso_sincronizado"] = 2
    assistidos = _preparar({"tardio": tardio, "antigo": antigo})
    for nome in ("Anime Tardio - S01E01.mkv", "Anime Tardio - S01E02.mkv", "Anime Antigo - S01E02.mkv"):
        _criar(os.path.join(assistidos, nome))

    consultas = []
    at.mal_client.obter_anime_por_id = lambda mal_id: (consultas.append(mal_id) or ({"id": mal_id, "title": "x", "num_episodes": 2}, None))
    chamadas_mal = []
    at.mal_client.atualizar_progresso = lambda anime_id, ep, status: (chamadas_mal.append((ep, status)) or (True, None))
    avisos = []
    at.definir_callback_anime_completo(lambda titulo, ep, removidos: avisos.append((titulo, ep, removidos)))

    at.sincronizar_progresso_mal()

    assert consultas == [1], f"só quem não tem total consulta o MAL: {consultas}"
    assert chamadas_mal == [(2, "completed")], chamadas_mal
    assert avisos == [("Anime Tardio", 2, 2)], avisos
    assert sorted(os.listdir(assistidos)) == ["2026 3-Verão", "Anime Antigo - S01E02.mkv"]
    registro = at._carregar_animes()["tardio"]
    assert registro["mal_num_episodios"] == 2 and "mal_conclusao_pendente" not in registro

    consultas.clear()
    chamadas_mal.clear()
    at.sincronizar_progresso_mal()
    assert consultas == [] and chamadas_mal == [], "conclusão não se repete"


def testar_total_ainda_desconhecido_consulta_uma_vez_por_dia():
    registro = _registro("Anime Aberto", None, 1)
    registro["mal_ultimo_progresso_sincronizado"] = 1
    _preparar({"aberto": registro})
    consultas = []
    at.mal_client.obter_anime_por_id = lambda mal_id: (consultas.append(mal_id) or ({"id": mal_id, "title": "x", "num_episodes": None}, None))
    at.mal_client.atualizar_progresso = lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia chamar o MAL"))

    at.sincronizar_progresso_mal()
    at.sincronizar_progresso_mal()

    assert consultas == [1], consultas


if __name__ == "__main__":
    for nome, funcao in list(globals().items()):
        if nome.startswith("testar_"):
            funcao()
            print(f"OK: {nome}")
    at.definir_callback_anime_completo(None)

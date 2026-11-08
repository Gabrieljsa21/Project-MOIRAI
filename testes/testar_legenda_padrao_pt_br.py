"""Teste: legenda pt-BR vira a faixa padrão do .mkv (2026-11-07, pedido do
usuário: "qnd inicio o episodio, eu tenho q trocar a legenda dele p pt-br").
Escolha da faixa sem rede nem ffmpeg; o remux real roda só se o ffmpeg
estiver no PATH (gera um .mkv minúsculo numa pasta temporária)."""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at


def _faixa(lang, title="", default=False):
    return {"lang": lang, "title": title, "default": default}


def testar_escolha_da_faixa():
    # Crunchyroll: inglês padrão, pt-BR no meio, português de Portugal também.
    faixas = [_faixa("eng", default=True), _faixa("por", "Portuguese (Portugal)"),
              _faixa("por", "Portuguese (Brazil)"), _faixa("spa")]
    assert at._posicao_legenda_pt_br(faixas) == 2
    # Completa ganha de letreiros.
    assert at._posicao_legenda_pt_br([_faixa("por", "Signs & Songs"), _faixa("por", "Full")]) == 1
    # Fansub sem título com "pob".
    assert at._posicao_legenda_pt_br([_faixa("eng"), _faixa("pob")]) == 1
    # Sem português (VARYG): nada a fazer.
    assert at._posicao_legenda_pt_br([_faixa("eng", default=True), _faixa("spa")]) is None


def testar_varredura_pula_torrent_ativo_e_o_que_ja_esta_certo():
    pasta = tempfile.mkdtemp()
    try:
        nomes = ["Anime A - S01E01.mkv", "Anime B - S01E02.mkv", "Anime C - S01E03.mkv",
                 "Anime D - S01E04.mkv", "Anime E - S01E05.mkv", "outro video.mkv"]
        for n in nomes:
            open(os.path.join(pasta, n), "wb").write(b"x")
        # Série do midia_tracker em subpasta da pasta de downloads: fica de fora.
        os.makedirs(os.path.join(pasta, "Series", "Serie"))
        open(os.path.join(pasta, "Series", "Serie", "Serie - S01E01.mkv"), "wb").write(b"x")
        caminho = lambda n: os.path.normcase(os.path.abspath(os.path.join(pasta, n)))
        faixas = {
            caminho(nomes[0]): [_faixa("eng", default=True), _faixa("por")],   # precisa mudar
            caminho(nomes[1]): [_faixa("eng"), _faixa("por", default=True)],   # já certo
            caminho(nomes[2]): [_faixa("eng", default=True)],                  # sem pt-BR
            caminho(nomes[3]): [_faixa("eng", default=True), _faixa("por")],   # semeando
            caminho(nomes[4]): None,                                          # áudio dublado em português
        }
        marcados = []
        at.obter_anime_pasta_downloads = lambda: pasta
        at.obter_anime_pasta_assistidos = lambda: ""
        at._arquivos_em_torrents_ativos = lambda: {caminho(nomes[3])}
        at._faixas_legenda = lambda c: faixas[c]
        at._marcar_legenda_padrao = lambda c, pos: marcados.append((os.path.basename(c), pos))
        at._legendas_conferidas.clear()
        assert [os.path.basename(c) for c, _ in at.padronizar_legendas_pt_br(dry_run=True)] == ["anime a - s01e01.mkv"]
        assert marcados == []
        at.padronizar_legendas_pt_br()
        assert marcados == [("anime a - s01e01.mkv", 1)], marcados
        # Segunda volta: tudo conferido, nada é lido de novo (o semeando continua pendente).
        at._faixas_legenda = lambda c: (_ for _ in ()).throw(AssertionError(c)) if c != caminho(nomes[3]) else faixas[c]
        at.padronizar_legendas_pt_br()
        assert len(marcados) == 1
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def testar_remux_real_com_ffmpeg():
    if not shutil.which("ffmpeg"):
        print("ffmpeg ausente, pulando remux real")
        return
    import importlib
    importlib.reload(at)
    pasta = tempfile.mkdtemp()
    try:
        srt = os.path.join(pasta, "s.srt")
        open(srt, "w", encoding="utf-8").write("1\n00:00:00,000 --> 00:00:01,000\nOi\n")
        mkv = os.path.join(pasta, "Anime - S01E01.mkv")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=1",
                        "-i", srt, "-i", srt, "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264",
                        "-c:s", "srt", "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=por",
                        "-disposition:s:0", "default", "-disposition:s:1", "0", mkv], check=True)
        antes = at._faixas_legenda(mkv)
        assert [f["default"] for f in antes] == [True, False], antes
        at._marcar_legenda_padrao(mkv, 1)
        depois = at._faixas_legenda(mkv)
        assert [f["default"] for f in depois] == [False, True], depois
        assert [f["lang"] for f in depois] == ["eng", "por"]
        assert os.listdir(pasta) == sorted(os.listdir(pasta)) and not any(n.endswith(".tmp") for n in os.listdir(pasta))
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


if __name__ == "__main__":
    testar_escolha_da_faixa()
    testar_varredura_pula_torrent_ativo_e_o_que_ja_esta_certo()
    testar_remux_real_com_ffmpeg()
    print("OK")

"""Teste: .mp4 baixado vira .mkv sozinho (2026-11-09, pedido do usuário:
"tem ep q ta em mp4 poe p converter p mkv qnd terminar de baixar").
Pasta temporária; o remux real só roda se o ffmpeg estiver no PATH."""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moirai.core import anime_tracker as at


def _mp4_real(caminho):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=1",
                    "-c:v", "libx264", caminho], check=True)


def testar_converte_so_o_que_terminou():
    if not shutil.which("ffmpeg"):
        print("ffmpeg ausente, pulando")
        return
    pasta = tempfile.mkdtemp()
    try:
        pronto = os.path.join(pasta, "Anime A - S01E05.mp4")
        semeando = os.path.join(pasta, "Anime B - S01E01.mp4")
        pessoal = os.path.join(pasta, "video das ferias.mp4")
        for c in (pronto, semeando, pessoal):
            _mp4_real(c)
        at.obter_anime_pasta_downloads = lambda: pasta
        at.obter_anime_pasta_assistidos = lambda: ""
        consultas = []
        at._arquivos_em_torrents_ativos = lambda: consultas.append(1) or {os.path.normcase(os.path.abspath(semeando))}

        convertidos = at.converter_mp4_baixados()
        assert [os.path.basename(m) for _, m in convertidos] == ["Anime A - S01E05.mkv"], convertidos
        assert sorted(os.listdir(pasta)) == ["Anime A - S01E05.mkv", "Anime B - S01E01.mp4", "video das ferias.mp4"]
        assert consultas == [1]

        # Saiu do qBittorrent: converte na volta seguinte.
        at._arquivos_em_torrents_ativos = lambda: consultas.append(1) or set()
        at.converter_mp4_baixados()
        assert sorted(os.listdir(pasta)) == ["Anime A - S01E05.mkv", "Anime B - S01E01.mkv", "video das ferias.mp4"]

        # Nada pendente: nem consulta o qBittorrent.
        antes = len(consultas)
        assert at.converter_mp4_baixados() == [] and len(consultas) == antes
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


if __name__ == "__main__":
    testar_converte_so_o_que_terminou()
    print("OK")

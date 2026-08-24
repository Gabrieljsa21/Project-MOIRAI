# -*- coding: utf-8 -*-
"""Entry point standalone do MOIRAI (`python -m moirai.main`) - extraído da GAIA em
2026-08-24 (ver `Project G.A.I.A/assistant/docs/TODO.md` -> "Arquitetura do
ecossistema"). Roda sozinho, sem nenhuma dependência da GAIA - continua checando
downloads em andamento/biblioteca local/progresso do MyAnimeList mesmo com a GAIA
fechada, exatamente como fazia dentro dela antes da extração
(`_monitorar_downloads_animes_loop`, `Project G.A.I.A/assistant/run.py`).

A checagem DIÁRIA de lançamentos novos (`executar_checagem_completa`) NÃO tem loop
próprio aqui de propósito - continua sendo a GAIA quem decide QUANDO rodar (seu
Agendador Diário já cuida da fila/lock/ordem entre vários avisos proativos) e O QUE
DIZER no Discord (valor de persona); o MOIRAI só expõe o resultado via HTTP
(`GET /checagem_diaria`, `moirai/api_bridge.py`) pra GAIA consultar quando quiser -
ver "Padrão GAIA → satélite (poll)" no TODO.md citado acima."""
import os
import socket
import sys
import threading
import time

from moirai import config
from moirai.api_bridge import iniciar_servidor_api
from moirai.core import anime_tracker

PORTA_INSTANCIA_UNICA = 8769
INTERVALO_LOOP_SEGUNDOS = 5 * 60

_socket_instancia_unica = None


def _garantir_instancia_unica():
    global _socket_instancia_unica
    _socket_instancia_unica = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _socket_instancia_unica.bind(("127.0.0.1", PORTA_INSTANCIA_UNICA))
    except OSError:
        print(
            " [SISTEMA] Já existe uma instância do MOIRAI rodando "
            f"(porta {PORTA_INSTANCIA_UNICA} ocupada) - encerrando esta pra não rodar em duplicidade."
        )
        sys.exit(1)


def _avisar_episodio_assistido_webhook(titulo, numero_episodio):
    """Callback registrado em `anime_tracker.definir_callback_episodio_movido_
    assistidos` - antes era uma chamada Python direta (mesmo processo da GAIA);
    como o MOIRAI roda separado agora, avisa por HTTP (webhook) pro endpoint novo
    da GAIA (`integrations/iris_bridge.py::POST /moirai/episodio_assistido`), se
    ela estiver de pé. Silencioso se a GAIA não estiver rodando (nunca trava o
    MOIRAI por causa de um aviso que ninguém vai ouvir)."""
    import json
    import urllib.request
    url = os.environ.get("MOIRAI_GAIA_WEBHOOK_URL", "http://127.0.0.1:8766/moirai/episodio_assistido")
    try:
        corpo = json.dumps({"titulo": titulo, "episodio": numero_episodio}).encode("utf-8")
        req = urllib.request.Request(url, data=corpo, method="POST", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def _loop_manutencao():
    """Mesmo trabalho de `_monitorar_downloads_animes_loop` (GAIA, antes da
    extração) - downloads em andamento, sincronia de biblioteca local,
    progresso do MyAnimeList, a cada 5min."""
    while True:
        try:
            if config.obter_anime_tracker_ativo():
                anime_tracker.verificar_downloads_em_andamento()
                anime_tracker.sincronizar_biblioteca_local()
                anime_tracker.sincronizar_progresso_mal()
        except Exception as e:
            print(f" [SISTEMA] MOIRAI: erro no loop de manutenção: {e}")
        time.sleep(INTERVALO_LOOP_SEGUNDOS)


def main():
    _garantir_instancia_unica()
    os.makedirs("data", exist_ok=True)

    anime_tracker.definir_callback_episodio_movido_assistidos(_avisar_episodio_assistido_webhook)

    thread_loop = threading.Thread(target=_loop_manutencao, daemon=True)
    thread_loop.start()

    print(" [SISTEMA] MOIRAI pronto - loop de manutenção a cada 5min, ponte HTTP na porta 8768.")
    iniciar_servidor_api()  # bloqueia a thread principal


if __name__ == "__main__":
    main()

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
ver "Padrão GAIA → satélite (poll)" no docs/TODO.md citado acima.

🔥 Exceção (2026-09-24, pedido do usuário depois de episódios perdidos numa
viagem): com a GAIA FECHADA, o próprio loop de manutenção roda a checagem
(`_checagem_autonoma_se_preciso`) quando a última tiver mais de
`anime_checagem_autonoma_intervalo_horas`. Com a GAIA de pé, nada muda - ela
continua decidindo quando checar. Os downloads iniciados/alertas de uma
checagem autônoma são entregues na próxima checagem da GAIA."""
import os
import socket
import sys
import threading
import time

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(caminho, override=False):
        """Fallback mínimo para instalações antigas ainda sem python-dotenv.

        O atualizador da GAIA troca o código do satélite sem necessariamente
        sincronizar a .venv. A ausência de uma dependência opcional de leitura
        do .env não pode impedir o MOIRAI inteiro de iniciar.
        """
        if not os.path.isfile(caminho):
            return False
        with open(caminho, "r", encoding="utf-8-sig") as arquivo:
            for linha in arquivo:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                if linha.startswith("export "):
                    linha = linha[7:].lstrip()
                chave, valor = linha.split("=", 1)
                chave = chave.strip()
                valor = valor.strip()
                if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
                    valor = valor[1:-1]
                if chave and (override or chave not in os.environ):
                    os.environ[chave] = valor
        return True

from moirai.paths import PASTA_PROJETO, PASTA_DADOS

load_dotenv(str(PASTA_PROJETO / ".env"), override=True)

from moirai import config
from moirai import runtime_log
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


def _gaia_rodando():
    """GAIA de pé = porta da ponte HTTP dela aceitando conexão (a mesma do
    webhook `MOIRAI_GAIA_WEBHOOK_URL`)."""
    import urllib.parse
    url = urllib.parse.urlparse(os.environ.get("MOIRAI_GAIA_WEBHOOK_URL", "http://127.0.0.1:8766/moirai/episodio_assistido"))
    try:
        with socket.create_connection((url.hostname or "127.0.0.1", url.port or 80), timeout=2):
            return True
    except OSError:
        return False


def _checagem_autonoma_se_preciso():
    if not config.obter_anime_checagem_autonoma_ativa() or _gaia_rodando():
        return
    horas = anime_tracker.horas_desde_ultima_checagem()
    if horas is not None and horas < config.obter_anime_checagem_autonoma_intervalo_horas():
        return
    print(" [SISTEMA] 🎬 GAIA fechada - MOIRAI rodando a checagem de lançamentos sozinho.")
    anime_tracker.executar_checagem_completa(origem="autonoma")


def _loop_downloads():
    """Downloads em andamento (concluído -> renomeia e marca "baixado";
    travado -> troca de magnet) a cada `anime_intervalo_downloads_segundos`
    (30s por padrão). Separado de _loop_manutencao em 2026-09-24, pedido do
    usuário: os episódios baixam em poucos minutos e a renomeação esperava
    até 5min pela próxima volta da manutenção. Sem download em andamento,
    verificar_downloads_em_andamento sai logo depois de ler o JSON local, sem
    falar com o qBittorrent."""
    while True:
        try:
            if config.obter_anime_tracker_ativo():
                with anime_tracker.lock_estado_animes:
                    anime_tracker.verificar_downloads_em_andamento()
        except Exception as e:
            print(f" [SISTEMA] MOIRAI: erro no loop de downloads: {e}")
        time.sleep(max(5, config.obter_anime_intervalo_downloads_segundos()))


def _loop_manutencao():
    """Mesmo trabalho de `_monitorar_downloads_animes_loop` (GAIA, antes da
    extração) - sincronia de biblioteca local e progresso do MyAnimeList, a
    cada 5min. + checagem autônoma de lançamentos quando a GAIA está fechada
    (2026-09-24). Downloads em andamento saíram daqui pro _loop_downloads."""
    while True:
        try:
            if config.obter_anime_tracker_ativo():
                with anime_tracker.lock_estado_animes:
                    anime_tracker.sincronizar_biblioteca_local()
                    anime_tracker.sincronizar_progresso_mal()
                _checagem_autonoma_se_preciso()
        except Exception as e:
            print(f" [SISTEMA] MOIRAI: erro no loop de manutenção: {e}")
        time.sleep(INTERVALO_LOOP_SEGUNDOS)


def main():
    runtime_log.ativar(str(PASTA_PROJETO))
    _garantir_instancia_unica()
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)

    anime_tracker.definir_callback_episodio_movido_assistidos(_avisar_episodio_assistido_webhook)

    threading.Thread(target=_loop_downloads, daemon=True).start()
    threading.Thread(target=_loop_manutencao, daemon=True).start()

    print(
        f" [SISTEMA] MOIRAI pronto - downloads a cada {config.obter_anime_intervalo_downloads_segundos()}s, "
        "manutenção a cada 5min, ponte HTTP na porta 8768."
    )
    iniciar_servidor_api()  # bloqueia a thread principal


if __name__ == "__main__":
    main()

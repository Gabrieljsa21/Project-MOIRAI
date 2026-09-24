"""Espelhamento simples de stdout/stderr para logs diários."""
import datetime
import os
import sys

# 🔥 2026-09-24: a pasta logs/ crescia sem limite (1 arquivo por dia, ~50-80KB
# nos dias de mais atividade).
DIAS_RETENCAO_LOGS = 30


def remover_logs_antigos(pasta_logs, dias=DIAS_RETENCAO_LOGS, hoje=None):
    """Apaga `AAAA-MM-DD.log` com data anterior a `dias` atrás. Só mexe em
    arquivo com nome exatamente nesse padrão; qualquer erro é ignorado (log
    nunca pode derrubar o processo)."""
    limite = (hoje or datetime.date.today()) - datetime.timedelta(days=dias)
    try:
        nomes = os.listdir(pasta_logs)
    except OSError:
        return
    for nome in nomes:
        try:
            data = datetime.date.fromisoformat(nome[:-4]) if nome.endswith(".log") else None
        except ValueError:
            continue
        if data and data < limite:
            try:
                os.remove(os.path.join(pasta_logs, nome))
            except OSError:
                pass


class RedirecionadorLog:
    def __init__(self, stream_original, pasta_projeto):
        self._stream_original = stream_original
        self._pasta_projeto = pasta_projeto
        self._arquivo = None
        self._data_arquivo = None
        self._inicio_de_linha = True

    def _garantir_arquivo(self):
        hoje = datetime.date.today().isoformat()
        if self._arquivo is not None and self._data_arquivo == hoje:
            return
        if self._arquivo is not None:
            try:
                self._arquivo.close()
            except Exception:
                pass
        try:
            pasta_logs = os.path.join(self._pasta_projeto, "logs")
            os.makedirs(pasta_logs, exist_ok=True)
            self._arquivo = open(os.path.join(pasta_logs, f"{hoje}.log"), "a", encoding="utf-8")
            self._data_arquivo = hoje
        except Exception:
            self._arquivo = None
        remover_logs_antigos(os.path.join(self._pasta_projeto, "logs"))

    def _com_horario(self, texto):
        partes = []
        for linha in texto.splitlines(keepends=True):
            if self._inicio_de_linha:
                partes.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ")
            partes.append(linha)
            self._inicio_de_linha = linha.endswith("\n")
        return "".join(partes)

    def write(self, texto):
        if self._stream_original:
            try:
                self._stream_original.write(texto)
            except Exception:
                pass
        self._garantir_arquivo()
        if self._arquivo:
            try:
                self._arquivo.write(self._com_horario(texto))
                self._arquivo.flush()
            except Exception:
                pass

    def flush(self):
        for destino in (self._stream_original, self._arquivo):
            if destino:
                try:
                    destino.flush()
                except Exception:
                    pass


def ativar(pasta_projeto):
    sys.stdout = RedirecionadorLog(sys.stdout, pasta_projeto)
    sys.stderr = RedirecionadorLog(sys.stderr, pasta_projeto)

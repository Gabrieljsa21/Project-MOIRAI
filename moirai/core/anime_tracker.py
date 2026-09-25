"""Assistente de Animes (2026-08-02, pedido do usuário) - acompanha os lançamentos de
episódios em https://darkmahou.io/ (scraping puro, sem API oficial - o site não tem
uma), avisa 1x por dia (ver _verificar_e_executar_animes_diario, run.py) quais animes ganharam
episódio novo, deixa o usuário marcar interesse/desinteresse por anime (ver
obter_animes_rastreados/marcar_interesse, usado pelo Painel - ui/qt_modais/animes.py),
e baixa automaticamente (via magnet, qBittorrent) os episódios dos animes marcados
"tenho_interesse", priorizando sem censura e 1080p HEVC.

Fluxo completo, em 4 etapas independentes (cada uma chamada por seu próprio loop em
run.py, mesmo espírito de separar "detectar" de "agir" já usado no resto do projeto -
ver _verificar_e_executar_lancamentos_steam_diario/_monitorar_precos_loop):
1. `verificar_novos_lancamentos()` - scraping da home (1x por dia) - atualiza o estado
   de cada anime (último episódio visto) e devolve os que ainda estão "pendente" (nem
   marcados com interesse, nem sem interesse) - ESSES continuam sendo informados TODO
   dia até o usuário decidir, pedido explícito do usuário ("os que ainda não marquei,
   continua me informando").
2. `processar_downloads_pendentes()` - pros marcados "tenho_interesse", se o último
   episódio visto ainda não foi baixado nem está baixando, extrai o magnet da página do
   anime e manda pro qBittorrent (`save_path` = obter_anime_pasta_downloads()).
3. `verificar_downloads_em_andamento()` - roda mais frequente (loop próprio, 30s por padrão desde 2026-09-24)
   - consulta o qBittorrent pelos hashes em andamento; quando um termina, renomeia o
   arquivo baixado pro padrão "{Título} - E{NN}{extensão}" e marca o episódio como
   "baixado".
4. `sincronizar_biblioteca_local()` - pedido do usuário (2026-08-02): "os que já
   assisti" ficam numa pasta separada (obter_anime_pasta_assistidos, ele move manual
   pra lá depois de assistir) - esta função só VARRE as duas pastas (downloads +
   assistidos) e atualiza o status de cada episódio (`"baixado"` vs `"assistido"`)
   com base em qual pasta o arquivo está agora, sem exigir nenhum passo extra do
   usuário além do que ele já faz (mover o arquivo quando termina de assistir).

Estrutura da página confirmada com scraping real em 2026-08-02 (ver mensagens da
sessão - não documentado num site de doc oficial, o HTML pode mudar sem aviso, então
todo parsing aqui é defensivo - devolve lista/None vazio em vez de lançar exceção pro
chamador quando a estrutura não bate com o esperado):
- Home (`https://darkmahou.io/`) tem uma seção "Últimos Lançamentos"
  (`div.bixbox.latestdark`, seguida de `div.listupd`) com um `article.bs` por anime -
  `.bsx > a[href]` é a URL da página do anime, `.ntitle` o título, `.epsx` o texto
  "Episódio NN" do último episódio.
- Cada página de anime tem uma seção "Baixar {Título}" com um `div.soraddl` POR
  episódio (`<h3>Episódio NN</h3>` seguido de uma tabela) - cada LINHA da tabela é um
  grupo de fonte (legendado/dublado/etc.), cada `<a href="magnet:...">` dentro dela é
  uma opção de qualidade, com o rótulo (ex.: "1080p HEVC") no próprio texto do link.
  Sempre pega a PRIMEIRA linha da tabela (legendado, a opção "padrão"/mais comum) -
  dublado fica de fora por padrão (não foi pedido, e a maioria dos releases mais
  rápidos/de melhor qualidade sai legendado primeiro).
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.parse
import winreg
import zipfile
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

import moirai.integrations.myanimelist.mal_client as mal_client
import moirai.integrations.anilist.anilist_client as anilist_client
from moirai.config import (
    obter_anime_pasta_downloads, obter_anime_pasta_assistidos, obter_mal_sync_ativo,
    obter_mal_confianca_minima, obter_mal_margem_minima, obter_anilist_limite_atraso_horas,
    obter_lembrete_limite_episodios, obter_lembrete_limite_dias,
    obter_renomear_confianca_minima, obter_renomear_confianca_parcial, obter_renomear_margem_parcial,
    obter_limiar_minutos_assistido, obter_anime_lembrete_atraso_ativo, obter_anime_notificar_pendentes_ativo,
    obter_anime_download_travado_horas, obter_anime_alerta_falha_horas,
)
from moirai.paths import caminho_dados

URL_BASE = "https://darkmahou.io"
ARQUIVO_ANIMES = caminho_dados("anime_tracker_animes.json")
ARQUIVO_CHECAGEM_DIARIA = caminho_dados("anime_tracker_checagem_diaria.json")
ARQUIVO_HISTORICO_CHECAGENS = caminho_dados("anime_tracker_historico_checagens.json")
# ~6 meses com a checagem por intervalo da GAIA (algumas por dia) - só pra o
# arquivo não crescer pra sempre.
_LIMITE_HISTORICO_CHECAGENS = 1000
CATEGORIA_QBITTORRENT = "gaia-animes"

# 🔥 Site protegido por Cloudflare mas sem desafio JS de verdade (testado 2026-08-02) -
# um User-Agent de navegador comum já basta, sem precisar de navegador automatizado.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_TIMEOUT_REQUEST = 20
# 🔥 2026-09-25, pedido do usuário ("tem muitos animes q parece q n vao
# baixar, tenta ver se em outra opcao baixa"): torrent sem NENHUM seeder
# conectado e sem progresso troca de opção em 2h, sem esperar as
# `anime_download_travado_horas` (24h) - com várias opções mortas em
# sequência, esperar 24h por cada uma levava dias.
_HORAS_TRAVADO_SEM_SEED = 2


def _obter_html_darkmahou(url):
    """GET numa página do DarkMahou, devolvendo o HTML já como texto UTF-8.
    Lança exceção em falha de rede/HTTP - cada chamador mantém o próprio
    try/except e mensagem de log.

    🔥 2026-09-24, bug real (usuário voltou de 1 semana fora e vários
    episódios não tinham baixado): o site passou a responder
    `Content-Type: text/html` SEM `charset` (a partir de 2026-09-23) - sem
    charset, o `requests` cai no padrão HTTP (ISO-8859-1) e "Episódio" vira
    "EpisÃ³dio". O regex `Epis[oó]dio` de _extrair_opcoes_download parava de
    casar e TODO download terminava em "Nenhum magnet encontrado"; títulos
    novos também eram gravados corrompidos ("4Âª Temporada"). O HTML do site
    é UTF-8 de verdade, então força a decodificação em vez de confiar no
    cabeçalho."""
    em_cache = _cache_html.get(url)
    if em_cache and time.time() - em_cache[0] < _SEGUNDOS_CACHE_HTML:
        return em_cache[1]
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT_REQUEST)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    _cache_html[url] = (time.time(), resp.text)
    return resp.text


# 🔥 2026-09-24: a mesma página de anime era baixada 1x por episódio (12
# requests pra um anime de 12 episódios) e agora também pra procurar
# especiais - 2 minutos cobrem uma checagem sem esconder mudança do site.
_cache_html = {}
_SEGUNDOS_CACHE_HTML = 120


EXTENSOES_VIDEO = (".mkv", ".mp4", ".avi")
PASTA_CAPAS = caminho_dados("anime_tracker_capas")


# ======================================================
# 📦 ESTADO PERSISTIDO
# ======================================================
def _carregar_animes():
    if not os.path.exists(ARQUIVO_ANIMES):
        return {}
    try:
        with open(ARQUIVO_ANIMES, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _salvar_animes(animes):
    os.makedirs(os.path.dirname(ARQUIVO_ANIMES), exist_ok=True)
    with open(ARQUIVO_ANIMES, "w", encoding="utf-8") as f:
        json.dump(animes, f, indent=4, ensure_ascii=False)


def obter_ultima_checagem_diaria():
    if os.path.exists(ARQUIVO_CHECAGEM_DIARIA):
        try:
            with open(ARQUIVO_CHECAGEM_DIARIA, "r", encoding="utf-8") as f:
                return json.load(f).get("ultima_data")
        except Exception:
            return None
    return None


def salvar_ultima_checagem_diaria(data_str):
    os.makedirs(os.path.dirname(ARQUIVO_CHECAGEM_DIARIA), exist_ok=True)
    with open(ARQUIVO_CHECAGEM_DIARIA, "w", encoding="utf-8") as f:
        json.dump({"ultima_data": data_str}, f, indent=4, ensure_ascii=False)


def _carregar_historico_checagens():
    try:
        with open(ARQUIVO_HISTORICO_CHECAGENS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def obter_historico_checagens(limite=None):
    """Registro de TODA checagem de lançamentos já feita (mais recente
    primeiro) - 2026-09-24, pedido do usuário: "manter registro dos dias e
    horários que checou animes novos". `anime_tracker_checagem_diaria.json`
    só guarda a ÚLTIMA data (é o gate de intervalo da GAIA), então depois de
    um período fora não dava pra saber quando o MOIRAI checou nem o que cada
    checagem encontrou/baixou/falhou. Formato de cada entrada: ver
    executar_checagem_completa."""
    historico = list(reversed(_carregar_historico_checagens()))
    return historico[:limite] if limite else historico


def _registrar_checagem(entrada):
    historico = _carregar_historico_checagens()
    historico.append(entrada)
    os.makedirs(os.path.dirname(ARQUIVO_HISTORICO_CHECAGENS), exist_ok=True)
    with open(ARQUIVO_HISTORICO_CHECAGENS, "w", encoding="utf-8") as f:
        json.dump(historico[-_LIMITE_HISTORICO_CHECAGENS:], f, indent=4, ensure_ascii=False)


def obter_animes_rastreados():
    """[(chave, registro), ...] - usado pelo Painel (ui/qt_modais/animes.py) pra
    listar todos os animes já vistos, com o status de interesse de cada um."""
    return list(_carregar_animes().items())


def obter_ultimos_episodios_por_status(registro):
    """(último lançado, último baixado, último assistido) - cada um int ou None.
    Pedido do usuário (2026-08-02): mostrar os 3 separados no Painel, não só "o
    episódio mais recente" misturado. "Baixado" conta episódio marcado `"baixado"`
    OU `"assistido"` (assistir pressupõe ter baixado antes) - `"assistido"` conta
    só o que está de fato na pasta de assistidos (ver sincronizar_biblioteca_local)."""
    episodios = registro.get("episodios", {})
    numeros_baixados = [int(n) for n, status in episodios.items() if status in ("baixado", "assistido")]
    numeros_assistidos = [int(n) for n, status in episodios.items() if status == "assistido"]
    return (
        registro.get("ultimo_episodio_visto"),
        max(numeros_baixados) if numeros_baixados else None,
        max(numeros_assistidos) if numeros_assistidos else None,
    )


def definir_ultimo_lancado(chave, numero):
    """Corrige manualmente o "último lançado" (2026-08-07, pedido do usuário:
    "permitir editar os 3 campos de último episódio" - pro caso do scraping
    errar ou demorar a refletir um episódio que já saiu). `numero` 0 (ou
    negativo) limpa de volta pra None (nenhum lançamento conhecido)."""
    animes = _carregar_animes()
    if chave in animes:
        animes[chave]["ultimo_episodio_visto"] = numero if numero > 0 else None
        _salvar_animes(animes)


def definir_ultimo_baixado(chave, numero):
    """Corrige manualmente o "último baixado" - marca `"baixado"` todo
    episódio de 1 até `numero` que ainda não estiver `"assistido"` (assistido
    é um status "maior", nunca regride pra baixado). `numero` 0 não desmarca
    nada retroativamente (só não avança mais nada)."""
    animes = _carregar_animes()
    if chave not in animes:
        return
    episodios = animes[chave].setdefault("episodios", {})
    for n in range(1, numero + 1):
        if episodios.get(str(n)) != "assistido":
            episodios[str(n)] = "baixado"
    _salvar_animes(animes)


def definir_ultimo_assistido(chave, numero):
    """Corrige manualmente o "último assistido" - marca `"assistido"` todo
    episódio de 1 até `numero` (assistido implica baixado, então sobrescreve
    qualquer status anterior desses números)."""
    animes = _carregar_animes()
    if chave not in animes:
        return
    episodios = animes[chave].setdefault("episodios", {})
    for n in range(1, numero + 1):
        episodios[str(n)] = "assistido"
    _salvar_animes(animes)


# 🔥 2026-08-14, pedido do usuário: "sem interesse" não precisa de capa nem
# de informação detalhada nenhuma (episódios/downloads/MAL/AniList/último
# episódio visto) - só o necessário pra identificar o anime na aba "Sem
# Interesse" (título/link) e pra não reaparecer em "Pendentes" (`interesse`
# != "pendente" já basta pra isso, ver verificar_novos_lancamentos).
_CAMPOS_MINIMOS_SEM_INTERESSE = ("titulo", "url", "interesse")


def marcar_interesse(chave, interesse):
    """`interesse`: "pendente" | "tenho_interesse" | "sem_interesse". Chamado pelo
    Painel quando o usuário clica um dos botões de interesse num anime.

    🔥 Marcar "sem_interesse" ENXUGA o registro pros campos mínimos (ver
    _CAMPOS_MINIMOS_SEM_INTERESSE acima) - verificar_novos_lancamentos
    respeita esse enxugamento e não bota capa/último episódio de volta
    enquanto o anime continuar "sem_interesse", mesmo que ele continue
    aparecendo em "Últimos Lançamentos" todo dia."""
    animes = _carregar_animes()
    if chave not in animes:
        return
    animes[chave]["interesse"] = interesse
    if interesse == "sem_interesse":
        registro = animes[chave]
        animes[chave] = {campo: registro[campo] for campo in _CAMPOS_MINIMOS_SEM_INTERESSE if campo in registro}
    _salvar_animes(animes)


def remover_anime_rastreado(chave):
    animes = _carregar_animes()
    if chave in animes:
        del animes[chave]
        _salvar_animes(animes)


def capa_local_cacheada(chave, capa_url):
    """Só a parte síncrona/rápida de obter_capa_local abaixo - devolve o
    caminho da capa SE ela já estiver em cache em disco, sem tocar rede, ou
    None se ainda precisa baixar. Existe pra quem chama (ui/qt_modais/
    animes.py::_carregar_capa_async) poder aproveitar o cache direto na
    thread principal, sem pagar o custo de spawnar uma thread à toa quando a
    resposta já está disponível na hora (2026-08-09, lentidão real medida ao
    abrir a tela com ~80 animes rastreados: 78 threads criadas só pra ler um
    arquivo que já existia em disco)."""
    if not capa_url:
        return None
    extensao = os.path.splitext(capa_url.split("?")[0])[1] or ".jpg"
    caminho = os.path.join(PASTA_CAPAS, f"{chave}{extensao}")
    return caminho if os.path.exists(caminho) else None


def obter_capa_local(chave, capa_url):
    """Caminho da capa em cache local desse anime (`data/anime_tracker_capas/
    <chave><extensão>`) - baixa 1x na primeira vez que for pedida (a capa de um
    anime praticamente nunca muda) e reaproveita depois, sem baixar de novo.
    Devolve None se não tiver `capa_url` ou se o download falhar - quem chama (Painel,
    ui/qt_modais/animes.py) simplesmente não mostra imagem nesse caso. Faz rede -
    sempre chamar via executar_em_thread (ui/qt_widgets.py) pra não travar a GUI."""
    cacheada = capa_local_cacheada(chave, capa_url)
    if cacheada:
        return cacheada
    if not capa_url:
        return None
    extensao = os.path.splitext(capa_url.split("?")[0])[1] or ".jpg"
    caminho = os.path.join(PASTA_CAPAS, f"{chave}{extensao}")
    try:
        resp = requests.get(capa_url, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT_REQUEST)
        resp.raise_for_status()
    except Exception as e:
        print(f" [SISTEMA] Erro ao baixar capa de {chave}: {e}")
        return None
    os.makedirs(PASTA_CAPAS, exist_ok=True)
    with open(caminho, "wb") as f:
        f.write(resp.content)
    return caminho


# ======================================================
# 🔎 SCRAPING - LISTA DE LANÇAMENTOS (home)
# ======================================================
def _chave_de_url(url):
    return url.rstrip("/").rsplit("/", 1)[-1]


def _numero_episodio_de_texto(texto):
    match = re.search(r"(\d+)", texto or "")
    return int(match.group(1)) if match else None


# ======================================================
# 📅 TEMPORADA DE ESTREIA (2026-08-14, pedido do usuário: agrupar Pendentes/
# Acompanhando por "Verão 2026"/"Outono 2026"/etc. no Painel)
# ======================================================
# 🔥 O bloco de info da página do anime (`div.spe`) tem um campo "Temporada:"
# (confirmado testando várias páginas reais em 2026-08-14) - o TEXTO exibido
# mistura idioma (às vezes "Primavera 2026", às vezes "Summer 2026",
# inconsistente no próprio DarkMahou), mas o SLUG do link
# (".../season/summer-2026/") é sempre em inglês e consistente - por isso
# _extrair_temporada_estreia sempre prefere o slug, só cai pro texto exibido
# (via _normalizar_nome_temporada) se não achar link nenhum.
_NOME_ESTACAO_POR_PALAVRA = {
    "winter": "Inverno", "inverno": "Inverno",
    "spring": "Primavera", "primavera": "Primavera",
    "summer": "Verão", "verao": "Verão", "verão": "Verão",
    "fall": "Outono", "autumn": "Outono", "outono": "Outono",
}
# 🔥 Ordem cronológica da estação DENTRO do ano, convenção da indústria de
# anime (cours: Jan-Mar Inverno, Abr-Jun Primavera, Jul-Set Verão, Out-Dez
# Outono - tradução literal do inglês Winter/Spring/Summer/Fall, não o
# calendário real de estações do Brasil) - usada só pra ORDENAR/comparar
# temporadas (chave_ordenacao_temporada/obter_temporada_atual).
_ORDEM_ESTACAO_NO_ANO = {"Inverno": 0, "Primavera": 1, "Verão": 2, "Outono": 3}
_MES_PARA_ESTACAO = {
    1: "Inverno", 2: "Inverno", 3: "Inverno",
    4: "Primavera", 5: "Primavera", 6: "Primavera",
    7: "Verão", 8: "Verão", 9: "Verão",
    10: "Outono", 11: "Outono", 12: "Outono",
}


def _normalizar_nome_temporada(texto):
    """'Summer 2026'/'Primavera 2026'/qualquer variação reconhecida -> sempre
    'Verão 2026' (nome fixo em português + ano). None se não reconhecer
    nem a estação nem o ano no texto."""
    match = re.search(r"([A-Za-zÀ-ÿ]+)\D*(\d{4})", texto or "")
    if not match:
        return None
    estacao = _NOME_ESTACAO_POR_PALAVRA.get(match.group(1).lower())
    return f"{estacao} {match.group(2)}" if estacao else None


def _extrair_temporada_estreia(soup):
    """Extrai a 'Temporada:' do bloco de info da página do anime (`div.spe`) -
    ex.: 'Verão 2026'. None se o bloco/campo não existir (estrutura mudou, ou
    a página não tiver esse dado)."""
    spe = soup.find("div", class_="spe")
    if not spe:
        return None
    for span in spe.find_all("span"):
        rotulo = span.find("b")
        if not rotulo or "Temporada" not in rotulo.get_text():
            continue
        link = span.find("a")
        href = link.get("href", "") if link else ""
        match = re.search(r"/season/(winter|spring|summer|fall|autumn)-(\d{4})", href)
        if match:
            return f"{_NOME_ESTACAO_POR_PALAVRA[match.group(1)]} {match.group(2)}"
        return _normalizar_nome_temporada(link.get_text(strip=True) if link else span.get_text(strip=True))
    return None


def _buscar_temporada_estreia(url):
    """(sucesso, temporada) - busca a PRÓPRIA página do anime só pra extrair a
    temporada de estreia (usado ao descobrir um anime novo, ver
    verificar_novos_lancamentos, e pelo backfill de quem já era rastreado
    antes dessa feature existir, ver backfill_temporadas_estreia).
    `sucesso=False` só em falha de REDE - quem chama não deve gravar nada
    nesse caso, pra tentar de novo na próxima checagem. `sucesso=True` com
    `temporada=None` é um resultado DEFINITIVO (página respondeu, campo não
    encontrado) - não tenta de novo depois disso."""
    try:
        html = _obter_html_darkmahou(url)
    except Exception as e:
        print(f" [SISTEMA] Erro ao buscar temporada de estreia ({url}): {e}")
        return False, None
    return True, _extrair_temporada_estreia(BeautifulSoup(html, "html.parser"))


def chave_ordenacao_temporada(temporada):
    """(ano, índice da estação no ano) - pra ordenar/comparar temporadas
    cronologicamente (ui/qt_modais/animes.py, agrupamento por temporada).
    `temporada` desconhecida/não reconhecida sempre ordena/compara como a
    mais ANTIGA possível ((-1, -1)), nunca como atual/futura."""
    match = re.match(r"(\w+)\s+(\d{4})", temporada or "")
    if not match:
        return (-1, -1)
    return (int(match.group(2)), _ORDEM_ESTACAO_NO_ANO.get(match.group(1), -1))


def obter_temporada_atual():
    """Temporada "atual" segundo o calendário de cours de anime (ver
    _MES_PARA_ESTACAO acima) - usada só pra decidir qual grupo de temporada
    fica expandido por padrão no Painel (ui/qt_modais/animes.py)."""
    agora = datetime.now()
    return f"{_MES_PARA_ESTACAO[agora.month]} {agora.year}"


# Pasta de anime finalizado (2026-09-24, pedido do usuário): "2026 3-Verão",
# o número na frente mantém as estações em ordem cronológica no Explorer.
_PADRAO_PASTA_TEMPORADA = re.compile(r"^\d{4} [1-4]-(?:Inverno|Primavera|Verão|Outono)$")


def nome_pasta_temporada(data=None):
    """'2026 3-Verão' para uma data de julho a setembro de 2026 (hoje, por padrão)."""
    data = data or datetime.now()
    estacao = _MES_PARA_ESTACAO[data.month]
    return f"{data.year} {_ORDEM_ESTACAO_NO_ANO[estacao] + 1}-{estacao}"


def anime_ja_finalizado(registro):
    """True para anime que já terminou de passar: filme, "Status: Completed"
    na página do DarkMahou (`status_site`), site já com o total de episódios
    do MAL, ou estreia 2 temporadas atrás ou antes.

    🔥 2026-09-25, pedido do usuário ("tem animes q acredito ja estarem
    completos q n foram movidos para pasta deles"): antes, anime da
    temporada atual nunca contava, mesmo com os 12 de 12 episódios (Youjo
    Senki II, Grand Blue 3...). O total do MAL agora vale em qualquer
    temporada; ainda sem o último episódio (Re:Zero 4, 18 de 19), fica de
    fora."""
    if registro.get("filme") or registro.get("status_site") == "Completed":
        return True
    total = registro.get("mal_num_episodios")
    if total and (registro.get("ultimo_episodio_visto") or 0) >= total:
        return True
    estreia = chave_ordenacao_temporada(registro.get("temporada_estreia"))
    if estreia == (-1, -1):
        return False
    atual = chave_ordenacao_temporada(obter_temporada_atual())
    return (atual[0] - estreia[0]) * 4 + atual[1] - estreia[1] >= 2


def _status_da_pagina(soup):
    """"Completed"/"Ongoing" do campo "Status:" da página do anime, ou None."""
    for span in soup.select(".spe span"):
        match = re.match(r"\s*Status:\s*(\w+)", span.get_text(" ", strip=True))
        if match:
            return match.group(1).capitalize()
    return None


_LIMITE_BACKFILL_TEMPORADA_POR_EXECUCAO = 20


def backfill_temporadas_estreia():
    """Preenche "temporada_estreia" de quem já era rastreado ANTES dessa
    feature existir (2026-08-14) - 1 request extra por anime SEM esse campo
    ainda (nunca pra "sem_interesse", que fica enxuto de propósito - ver
    _CAMPOS_MINIMOS_SEM_INTERESSE), até _LIMITE_BACKFILL_TEMPORADA_POR_EXECUCAO
    por execução (não trava o loop diário buscando dezenas de páginas de uma
    vez só - o resto fica pra próxima checagem; o campo é permanente uma vez
    preenchido, nunca precisa buscar esse anime de novo). Chamada por
    executar_checagem_completa(). Devolve quantos preencheu (só informativo)."""
    animes = _carregar_animes()
    alvos = [
        (chave, registro) for chave, registro in animes.items()
        if registro.get("interesse") != "sem_interesse" and "temporada_estreia" not in registro and registro.get("url")
    ]
    if not alvos:
        return 0
    preenchidos = 0
    for chave, registro in alvos[:_LIMITE_BACKFILL_TEMPORADA_POR_EXECUCAO]:
        sucesso, temporada_estreia = _buscar_temporada_estreia(registro["url"])
        if sucesso:
            registro["temporada_estreia"] = temporada_estreia
            preenchidos += 1
    if preenchidos:
        _salvar_animes(animes)
    return preenchidos


def listar_ultimos_lancamentos():
    """Scraping real da seção "Últimos Lançamentos" da home do DarkMahou - devolve
    [{"titulo", "episodio" (int ou None), "url"}, ...]. Lista vazia em qualquer falha
    (rede, estrutura da página mudou) - nunca lança exceção pro chamador, mesmo
    espírito defensivo do resto do projeto (ex.: buscar_noticias_topico,
    features/jornalista/jornalista.py)."""
    try:
        html = _obter_html_darkmahou(URL_BASE)
    except Exception as e:
        print(f" [SISTEMA] Erro ao acessar DarkMahou (lançamentos): {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    secao = soup.find("div", class_="latestdark")
    listupd = secao.find_next("div", class_="listupd") if secao else None
    if listupd is None:
        print(" [SISTEMA] DarkMahou: seção 'Últimos Lançamentos' não encontrada - o site pode ter mudado de layout.")
        return []

    itens = []
    for artigo in listupd.find_all("article", class_="bs"):
        link = artigo.find("a", href=True)
        titulo_span = artigo.find("span", class_="ntitle")
        episodio_span = artigo.find("span", class_="epsx")
        capa_img = artigo.find("img", src=True)
        if not link or not titulo_span:
            continue
        itens.append({
            "titulo": titulo_span.get_text(strip=True),
            "episodio": _numero_episodio_de_texto(episodio_span.get_text(strip=True)) if episodio_span else None,
            "url": link["href"],
            "capa_url": capa_img["src"] if capa_img else None,
        })
    return itens


# 🔥 Só "Episódio N" conta como número de episódio (2026-09-24, caso real
# Kimi no Koto ga Daidaidaidaidaisuki na 100-nin no Kanojo): a página tem um
# bloco único com o NOME do anime (pacote da temporada) e o primeiro número do
# título ("100-nin") virava "último episódio = 100". Lote ("Episódios 01~04")
# e título de anime ficam de fora.
# Um caractere qualquer no lugar do "s" (caso real Re:Zero 4ª Temporada: o
# site publicou "Epi8ódio 18" e o E18 nunca era encontrado). "Episódios"
# (lote) continua de fora porque exige espaço logo depois de "dio".
_PALAVRA_EPISODIO = r"Epi.?[oó]dio"
_PADRAO_BLOCO_NUMERADO = re.compile(rf"^{_PALAVRA_EPISODIO}\s+0*(\d+)\b", re.IGNORECASE)
# 🔥 2026-09-25, caso real Boku no Kokoro no Yabai Yatsu Movie: bloco único
# "Filme Completo Legendado Torrent" caía no fluxo de pacote da temporada,
# que descartava o torrent por não ter vídeo numerado. Filme é o episódio 1
# de um anime de 1 episódio só (`registro["filme"]`).
_PADRAO_BLOCO_FILME = re.compile(r"^Filme\b", re.IGNORECASE)


def _numero_do_bloco(texto_bloco):
    if _PADRAO_BLOCO_ESPECIAL.match(texto_bloco or ""):
        return None  # "Episódio 00" é especial (ver _PADRAO_BLOCO_ESPECIAL)
    if _PADRAO_BLOCO_FILME.match(texto_bloco or ""):
        return 1
    m = _PADRAO_BLOCO_NUMERADO.match(texto_bloco or "")
    return int(m.group(1)) if m else None


def _pagina_eh_filme(soup):
    """True se a página tem bloco "Filme ..." e nenhum "Episódio N"."""
    textos = [b.find("h3").get_text(strip=True) for b in soup.find_all("div", class_="soraddl") if b.find("h3")]
    return (any(_PADRAO_BLOCO_FILME.match(t) for t in textos)
            and not any(_PADRAO_BLOCO_NUMERADO.match(t) and _numero_do_bloco(t) is not None for t in textos))


def _ultimo_episodio_da_pagina(soup):
    """MAIOR número entre os blocos "Episódio N" (`div.soraddl`) da página do
    anime (não assume que vêm em ordem), incluindo os episódios de zips de
    .torrent do Yandex (_episodios_em_zips_yandex). None se não achar nenhum."""
    numeros = [
        _numero_do_bloco(bloco.find("h3").get_text(strip=True))
        for bloco in soup.find_all("div", class_="soraddl") if bloco.find("h3")
    ]
    numeros = [n for n in numeros if n is not None] + list(_episodios_em_zips_yandex(soup))
    return max(numeros) if numeros else None


def _precisa_consultar_pagina(registro):
    """Anime acompanhado que pode ter episódio novo fora da home - pula quem
    já lançou tudo que o MAL diz que existe (temporada encerrada), pra não
    gastar 1 request por anime terminado a cada checagem."""
    if registro.get("interesse") != "tenho_interesse" or registro.get("status_site") == "Completed":
        return False
    total = registro.get("mal_num_episodios")
    ultimo = registro.get("ultimo_episodio_visto")
    return not (total and ultimo and ultimo >= total)


def _atualizar_lancamentos_fora_da_home(animes, chaves_na_home, relatorio):
    """Consulta a PÁGINA de cada anime "tenho_interesse" que NÃO apareceu na
    home nesta checagem e atualiza `ultimo_episodio_visto` se houver episódio
    mais novo.

    🔥 2026-09-24, bug real (usuário ficou 1 semana fora): "Últimos
    Lançamentos" é uma janela rotativa de ~20 vagas. Com o PC desligado
    vários dias, o episódio da semana de cada anime entrava e saía da home
    sem nenhuma checagem ver - ao voltar, `ultimo_episodio_visto` continuava
    parado e _episodios_a_baixar não tinha gap nenhum pra fechar (10 animes
    ficaram 1-2 episódios atrás). A página do anime lista TODOS os episódios,
    então é a fonte que não depende de a checagem acontecer na hora certa.
    Só avança o número, nunca regride (página com layout quebrado não apaga
    o que já se sabia)."""
    for chave, registro in animes.items():
        if chave in chaves_na_home or not _precisa_consultar_pagina(registro):
            continue
        try:
            html = _obter_html_darkmahou(registro["url"])
        except Exception as e:
            print(f" [SISTEMA] Erro ao consultar página de {registro['titulo']}: {e}")
            relatorio["paginas_com_erro"].append(registro["titulo"])
            continue
        relatorio["paginas_consultadas"] += 1
        soup = BeautifulSoup(html, "html.parser")
        registro["status_site"] = _status_da_pagina(soup) or registro.get("status_site")  # anime_ja_finalizado
        ultimo_pagina = _ultimo_episodio_da_pagina(soup)
        anterior = registro.get("ultimo_episodio_visto")
        if ultimo_pagina is not None and (anterior is None or ultimo_pagina > anterior):
            registro["ultimo_episodio_visto"] = ultimo_pagina
            relatorio["episodios_novos"].append(
                {"titulo": registro["titulo"], "de": anterior, "para": ultimo_pagina, "fonte": "pagina"}
            )
            print(f" [SISTEMA] 🎬 {registro['titulo']}: Episódio {ultimo_pagina} encontrado na página do anime (fora da home).")


def verificar_novos_lancamentos(relatorio=None):
    """Roda 1x por dia (ver _verificar_e_executar_animes_diario, run.py). Atualiza o estado de
    CADA anime visto na home com o último episódio (mesmo os já marcados
    interesse/sem interesse - o estado precisa continuar atual pra
    processar_downloads_pendentes saber se tem episódio novo). Devolve só os
    "pendente" (nem marcados com nem sem interesse) - pedido do usuário: esses
    precisam continuar sendo informados TODO dia até serem marcados.

    Os "tenho_interesse" que não estão na home são conferidos pela própria
    página (_atualizar_lancamentos_fora_da_home). `relatorio` (dict, opcional)
    recebe o que esta checagem encontrou - usado pelo histórico de checagens
    (executar_checagem_completa)."""
    if relatorio is None:
        relatorio = {}
    relatorio.update({"itens_home": 0, "paginas_consultadas": 0, "paginas_com_erro": [], "episodios_novos": []})
    animes = _carregar_animes()
    pendentes = []
    itens_home = listar_ultimos_lancamentos()
    relatorio["itens_home"] = len(itens_home)
    chaves_na_home = set()
    for item in itens_home:
        chave = _chave_de_url(item["url"])
        chaves_na_home.add(chave)
        registro = animes.get(chave)
        if registro is None:
            registro = {
                "titulo": item["titulo"],
                "url": item["url"],
                "capa_url": item.get("capa_url"),
                "interesse": "pendente",
                "ultimo_episodio_visto": None,
                # 🔥 "episodios" (2026-08-02, "manter controle dos episódios
                # disponíveis... os que já baixei, e os que já assisti") - dict
                # {"numero": "baixado"|"assistido"}, mantido por
                # sincronizar_biblioteca_local (varre as 2 pastas reais no disco,
                # nunca marcado manualmente pelo usuário).
                "episodios": {},
                "downloads_em_andamento": {},
            }
            # 🔥 Busca a temporada de estreia SÓ na 1ª vez que esse anime é
            # visto (2026-08-14, pedido do usuário) - 1 request extra, mas só
            # pros poucos títulos NOVOS de cada checagem, não pra janela toda
            # de "Últimos Lançamentos". Só grava em sucesso - falha de rede
            # fica pro backfill_temporadas_estreia tentar de novo depois.
            sucesso, temporada_estreia = _buscar_temporada_estreia(item["url"])
            if sucesso:
                registro["temporada_estreia"] = temporada_estreia
            animes[chave] = registro
        registro["titulo"] = item["titulo"]
        registro["url"] = item["url"]
        # 🔥 "sem_interesse" fica ENXUTO de propósito (marcar_interesse,
        # 2026-08-14, pedido do usuário) - não bota capa nem último episódio
        # visto de volta nele, mesmo que o anime continue aparecendo em
        # "Últimos Lançamentos" todo dia (só titulo/url acima, sempre
        # atualizados - o resto ficaria de fora justamente pra manter o
        # registro enxuto).
        if registro.get("interesse") != "sem_interesse":
            if item.get("capa_url"):
                registro["capa_url"] = item["capa_url"]
            anterior = registro.get("ultimo_episodio_visto")
            if item["episodio"] is not None:
                registro["ultimo_episodio_visto"] = item["episodio"]
                if registro.get("interesse") == "tenho_interesse" and (anterior is None or item["episodio"] > anterior):
                    relatorio["episodios_novos"].append(
                        {"titulo": registro["titulo"], "de": anterior, "para": item["episodio"], "fonte": "home"}
                    )
        if registro.get("interesse", "pendente") == "pendente":
            pendentes.append((chave, registro))
    _atualizar_lancamentos_fora_da_home(animes, chaves_na_home, relatorio)
    _salvar_animes(animes)
    return pendentes


def adicionar_anime_manual(url):
    """Adiciona (ou atualiza) um anime rastreado diretamente pela URL da
    página dele no DarkMahou - pro caso em que o anime não está (ou já saiu)
    da janela rotativa de "Últimos Lançamentos" (~20 vagas, ver
    _episodios_a_baixar), e o usuário já sabe o link de cor (pedido real do
    usuário, 2026-08-05: "põe a Gaia pra baixar esse anime <link>"). Faz
    scraping da PRÓPRIA página do anime (título via `<h1>`, capa via
    `div.thumb img`, e o ÚLTIMO episódio pego como o MAIOR número entre todos
    os blocos `div.soraddl` - não assume que vêm em ordem) e marca
    "tenho_interesse" direto (é isso que "baixar" significa aqui, não só
    "ficar de olho"). Devolve (chave, erro) - erro None em caso de sucesso,
    chave None em caso de falha (rede, página não encontrada, estrutura
    mudou)."""
    try:
        html = _obter_html_darkmahou(url)
    except Exception as e:
        return None, f"Erro ao acessar a página do anime: {e}"

    soup = BeautifulSoup(html, "html.parser")
    titulo_tag = soup.find("h1")
    if not titulo_tag:
        return None, "Não achei o título na página - a estrutura do site pode ter mudado."
    titulo = titulo_tag.get_text(strip=True)

    ultimo_episodio = _ultimo_episodio_da_pagina(soup)

    thumb = soup.find("div", class_="thumb")
    capa_img = thumb.find("img", src=True) if thumb else None
    capa_url = capa_img["src"] if capa_img else None

    chave = _chave_de_url(url)
    animes = _carregar_animes()
    registro = animes.get(chave, {
        "episodios": {},
        "downloads_em_andamento": {},
    })
    registro["titulo"] = titulo
    registro["url"] = url
    if capa_url:
        registro["capa_url"] = capa_url
    registro["interesse"] = "tenho_interesse"
    if ultimo_episodio is not None:
        registro["ultimo_episodio_visto"] = ultimo_episodio
    registro["filme"] = _pagina_eh_filme(soup)  # 🔥 2026-09-25: o Painel pula a seleção de episódios
    registro["status_site"] = _status_da_pagina(soup)
    registro["temporada_estreia"] = _extrair_temporada_estreia(soup)  # 🔥 2026-08-14 - já temos o soup, sem request extra
    animes[chave] = registro
    _salvar_animes(animes)
    return chave, None


def formatar_texto_pendentes(pendentes):
    """Texto pronto pra notificar (Discord/log) - None se não há nada pendente hoje
    (mesmo padrão de "some do resumo, sem frase de aviso" já usado no Modo
    Jornalista - silêncio é a resposta certa quando não há nada de novo)."""
    linhas = [
        f"- {registro['titulo']} - Episódio {registro['ultimo_episodio_visto']}"
        for _, registro in pendentes
        if registro.get("ultimo_episodio_visto") is not None
    ]
    if not linhas:
        return None
    return (
        "🎬 Animes com episódio novo (marque interesse no Painel - 🎭 ícone \"Animes\"):\n"
        + "\n".join(linhas)
    )


# ======================================================
# 🔎 SCRAPING - MAGNET DE UM EPISÓDIO (página do anime)
# ======================================================
_PADRAO_SEM_CENSURA = re.compile(r"sem\s+censura|uncensored", re.IGNORECASE)


def _escolher_melhor_magnet(opcoes, numero_episodio=None):
    """`opcoes`: [(rotulo, magnet), ...] da primeira linha (legendado) da tabela de
    download. Prioriza 1080p+HEVC > 1080p (qualquer encoder) > primeira opção
    disponível, nessa ordem - pedido do usuário ("priorizando os 1080p HEVC").

    🔥 Desempate (2026-09-24, caso real Yomi no Tsugai): entre opções da MESMA
    qualidade, prefere a que declara no próprio nome (`dn` do magnet) o
    número do episódio pedido. A Judas numera Yomi 2 atrás do site (bloco
    "Episódio 23" com "S01E21"), o que travava a renomeação; DKB/Erai no mesmo
    bloco usam a numeração do site. A qualidade continua mandando: o
    desempate nunca troca 1080p HEVC por uma opção pior.

    🔥 Sem censura acima de tudo (2026-09-24, pedido do usuário: "se puder
    escolher, prefiro sem censura"): a versão sem censura fica na MESMA linha
    da tabela que as outras, com rótulo inconsistente (caso real Haite
    Kudasai, Takamine-san: "1080p Sem Censura", "1080p PT-BR SEM CENSURA" e
    até "1080p Censura" com `[UNCENSORED]` no nome). Por isso confere rótulo E
    `dn`. Vem antes da qualidade porque o rótulo quase nunca diz "HEVC" mesmo
    quando o arquivo é x265 - depois dela, nunca seria escolhida.

    Opções de lote (3º elemento `(inicio, fim)`, ver _extrair_opcoes_download)
    perdem para episódio avulso no mesmo nível, e lote menor ganha de lote
    maior - só o arquivo pedido é baixado, mas lote menor é menos torrent
    pra buscar metadado e semear."""
    if not opcoes:
        return None

    def tamanho_lote(par):
        lote = par[2] if len(par) > 2 else None
        return lote[1] - lote[0] + 1 if lote else 1

    def sem_censura(rotulo, magnet):
        return 1 if _opcao_sem_censura(rotulo, magnet) else 0

    def pontuar(rotulo):
        rotulo_min = rotulo.lower()
        if "1080p" in rotulo_min and "hevc" in rotulo_min:
            return 2
        if "1080p" in rotulo_min:
            return 1
        return 0

    def numero_bate(magnet):
        if numero_episodio is None:
            return 0
        return 1 if _numero_episodio_no_nome_arquivo(_nome_da_opcao(magnet)) == int(numero_episodio) else 0

    # max() devolve o PRIMEIRO empate - mantém a ordem da página como último critério.
    return max(opcoes, key=lambda par: (
        sem_censura(par[0], par[1]), pontuar(par[0]), numero_bate(par[1]), -tamanho_lote(par)))[1]


def _opcao_sem_censura(rotulo, magnet):
    return bool(_PADRAO_SEM_CENSURA.search(f"{rotulo} {_nome_da_opcao(magnet)}"))


# ======================================================
# 🔗 LINK .torrent (nyaa.si) ALÉM DE MAGNET
# ======================================================
# 🔥 2026-09-24, caso real Kawaii dake ja Nai Shikimori-san: páginas mais
# antigas do DarkMahou linkam `https://nyaa.si/download/N.torrent` em vez de
# magnet, e todo episódio terminava em "nenhum magnet na página". O resto do
# fluxo depende do hash (acompanhamento, magnets tentados), então o .torrent é
# baixado aqui, uma vez por processo, e o hash sai do SHA-1 do dicionário
# `info`, como no próprio BitTorrent.
_PADRAO_LINK_DOWNLOAD = re.compile(r"^magnet:|\.torrent(?:$|\?)", re.IGNORECASE)
_cache_torrents = {}
_SEGUNDOS_CACHE_FALHA_TORRENT = 3600


def _bdecode(dados, i=0):
    """Decodifica um valor bencode a partir de `i`; devolve (valor, fim).
    Em dicionário, guarda o trecho cru de `info` em `_info_bruto`."""
    c = dados[i:i + 1]
    if c == b"i":
        fim = dados.index(b"e", i)
        return int(dados[i + 1:fim]), fim + 1
    if c == b"l":
        i, lista = i + 1, []
        while dados[i:i + 1] != b"e":
            valor, i = _bdecode(dados, i)
            lista.append(valor)
        return lista, i + 1
    if c == b"d":
        i, dicio = i + 1, {}
        while dados[i:i + 1] != b"e":
            chave, i = _bdecode(dados, i)
            inicio_valor = i
            dicio[chave], i = _bdecode(dados, i)
            if chave == b"info":
                dicio["_info_bruto"] = dados[inicio_valor:i]
        return dicio, i + 1
    separador = dados.index(b":", i)
    tamanho = int(dados[i:separador])
    return dados[separador + 1:separador + 1 + tamanho], separador + 1 + tamanho


def _obter_torrent(url):
    """(bytes, hash, nome) de um link .torrent, ou None em qualquer falha.
    Falha fica em cache por `_SEGUNDOS_CACHE_FALHA_TORRENT`: o nyaa.si remove
    torrent (caso real: todo "1080p HEVC" do Shikimori-san dá 404) e a
    mesma opção é consultada várias vezes por escolha."""
    em_cache = _cache_torrents.get(url)
    if em_cache and (em_cache[1] is not None or time.time() - em_cache[0] < _SEGUNDOS_CACHE_FALHA_TORRENT):
        return em_cache[1]
    momento = time.time()
    try:
        if url.startswith(_PREFIXO_YANDEX_ZIP):
            url_publica, nome_arquivo = url[len(_PREFIXO_YANDEX_ZIP):].split("#", 1)
            conteudo = _torrents_do_zip_yandex(url_publica)[nome_arquivo]
        else:
            conteudo = _get_nyaa_com_espelho(url, params=None).content
        meta, _ = _bdecode(conteudo)
        nome = meta[b"info"].get(b"name", b"").decode("utf-8", errors="replace")
        resultado = (conteudo, hashlib.sha1(meta["_info_bruto"]).hexdigest(), nome)
    except Exception as e:
        print(f" [SISTEMA] .torrent indisponível ({url}): {e}")
        resultado = None
        if isinstance(e, (requests.ConnectionError, requests.Timeout)):
            # Rede fora não é torrent removido: tenta de novo em 5 min, não em 1h.
            momento -= _SEGUNDOS_CACHE_FALHA_TORRENT - _SEGUNDOS_CACHE_FALHA_REDE
    _cache_torrents[url] = (momento, resultado)
    return resultado


# 🔥 2026-09-25 (caso real, 15h18): o nyaa.si parou de responder bem na hora
# da troca dos travados de Shinobi no Ittoki - cada .torrent levava 20s de
# timeout e o MOIRAI concluía "sem outra opção". O espelho nyaa.land serve os
# mesmos .torrent (o RSS dele fica atrás do desafio do Cloudflare, então a
# busca de _buscar_nyaa só ganha com a marcação de "fora"). Com o nyaa.si
# fora, ele é pulado por `_SEGUNDOS_NYAA_FORA`.
_PADRAO_HOST_NYAA = re.compile(r"^https?://nyaa\.si(?=/)", re.IGNORECASE)
_ESPELHO_NYAA = "https://nyaa.land"
_SEGUNDOS_NYAA_FORA = 600
_SEGUNDOS_CACHE_FALHA_REDE = 300
_nyaa_si_fora_ate = [0.0]


def _get_nyaa_com_espelho(url, params):
    """requests.get com o espelho do nyaa quando o nyaa.si não responde
    (erro HTTP de verdade, como 404, não passa pro espelho). Qualquer outra
    URL vai direto."""
    tentativas = [url]
    if _PADRAO_HOST_NYAA.match(url):
        espelho = _PADRAO_HOST_NYAA.sub(_ESPELHO_NYAA, url)
        tentativas = [espelho] if time.time() < _nyaa_si_fora_ate[0] else [url, espelho]
    for i, alvo in enumerate(tentativas):
        try:
            resp = requests.get(alvo, params=params, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT_REQUEST)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout):
            if i == len(tentativas) - 1:
                raise
            _nyaa_si_fora_ate[0] = time.time() + _SEGUNDOS_NYAA_FORA
            print(f" [SISTEMA] nyaa.si sem resposta - usando {_ESPELHO_NYAA} pelos próximos {_SEGUNDOS_NYAA_FORA // 60} min.")


def _nome_da_opcao(link):
    """Nome do conteúdo declarado no link: `dn` do magnet ou `name` do .torrent."""
    if link.startswith("magnet:"):
        return urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("dn", [""])[0]
    torrent = _obter_torrent(link)
    return torrent[2] if torrent else ""


def _hash_da_opcao(link):
    if link.startswith("magnet:"):
        return _hash_do_magnet(link)
    torrent = _obter_torrent(link)
    return torrent[1] if torrent else None


# ======================================================
# 📦 ZIP DE .torrent NO YANDEX DISK (bloco só com link direto)
# ======================================================
# 🔥 2026-09-25, pedido do usuário ("se adapta p atender esse tipo ai tbm
# do bloco", caso real Boku no Kokoro no Yabai Yatsu 2ª Temporada): o bloco
# "Temporada Completa" só tem Yandex/Jottacloud, mas o Yandex é um zip de
# ~400 KB com 1 .torrent por episódio. A API pública do Yandex Disk dá o
# arquivo sem login; cada .torrent vira a opção do seu episódio, com o link
# sintético "yandex-zip:<url>#<arquivo>" que _obter_torrent resolve - daí
# em diante é o fluxo normal de .torrent (hash, qBittorrent, renomeação).
_PREFIXO_YANDEX_ZIP = "yandex-zip:"
_PADRAO_LINK_YANDEX = re.compile(r"^https?://(?:disk\.yandex\.[a-z.]+|yadi\.sk)/[di]/", re.IGNORECASE)
_API_YANDEX_PUBLICO = "https://cloud-api.yandex.net/v1/disk/public/resources"
_TAMANHO_MAX_ZIP_YANDEX = 20 * 1024 * 1024  # zip de .torrent tem KB; maior que isso é vídeo, fora do escopo
_cache_zips_yandex = {}


def _torrents_do_zip_yandex(url_publica):
    """{nome_do_arquivo: bytes} dos .torrent dentro do zip público do Yandex
    Disk; {} em qualquer falha (pasta, zip grande, rede). Mesmo cache de
    _obter_torrent: sucesso vale o processo inteiro, falha 1h."""
    em_cache = _cache_zips_yandex.get(url_publica)
    if em_cache and (em_cache[1] or time.time() - em_cache[0] < _SEGUNDOS_CACHE_FALHA_TORRENT):
        return em_cache[1]
    torrents = {}
    try:
        resp = requests.get(_API_YANDEX_PUBLICO, params={"public_key": url_publica}, timeout=_TIMEOUT_REQUEST)
        resp.raise_for_status()
        meta = resp.json()
        if (meta.get("type") == "file" and meta.get("name", "").lower().endswith(".zip")
                and meta.get("size", 0) <= _TAMANHO_MAX_ZIP_YANDEX and meta.get("file")):
            arquivo = requests.get(meta["file"], headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT_REQUEST)
            arquivo.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(arquivo.content)) as zip_:
                for info in zip_.infolist():
                    if info.filename.lower().endswith(".torrent"):
                        torrents[os.path.basename(info.filename)] = zip_.read(info)
    except Exception as e:
        print(f" [SISTEMA] Zip do Yandex indisponível ({url_publica}): {e}")
    _cache_zips_yandex[url_publica] = (time.time(), torrents)
    return torrents


def _opcoes_zip_yandex_do_bloco(bloco):
    """[(numero, rotulo, link_sintético)] dos .torrent com número de episódio
    no nome, de todo link do Yandex no bloco."""
    texto_bloco = bloco.find("h3").get_text(strip=True) if bloco.find("h3") else ""
    opcoes = []
    for a in bloco.find_all("a", href=_PADRAO_LINK_YANDEX):
        for nome in _torrents_do_zip_yandex(a["href"]):
            numero = _numero_episodio_no_nome_arquivo(re.sub(r"\.torrent$", "", nome, flags=re.IGNORECASE))
            if numero is not None:
                opcoes.append((numero, f"{a.get_text(strip=True)} ({texto_bloco}: {nome})",
                               f"{_PREFIXO_YANDEX_ZIP}{a['href']}#{nome}"))
    return opcoes


# ======================================================
# 🔎 NYAA.SI - RESERVA QUANDO A PÁGINA NÃO TEM MAIS OPÇÃO
# ======================================================
# 🔥 2026-09-25, pedido do usuário ("tem bastante ep ainda com 0%, ja
# procurou mais opcoes p eles?"): Shinobi no Ittoki 06 e Shikimori-san 12
# esgotaram as opções da página do DarkMahou, todas sem seeder, mas o
# nyaa.si tinha Erai-raws com seeders. A busca (RSS, que já traz o número
# de seeders) só roda quando a página não tem mais opção não tentada, e
# só aceita o que manteria o padrão do MOIRAI: legendado em português
# (POR-BR/PT-BR, ou "Multiple Subtitle" da Erai, que inclui POR-BR), mesma
# temporada e episódio, com seeder, sem dublagem nem batch.
_URL_RSS_NYAA = "https://nyaa.si/"
_NS_NYAA = "{https://nyaa.si/xmlns/nyaa}"
_PADRAO_LEGENDA_PT_BR = re.compile(r"POR-BR|PT-BR|Multiple Subtitle", re.IGNORECASE)
_PADRAO_NYAA_DESCARTAR = re.compile(r"\bdub\b|\bdual[- ]audio\b|\bbatch\b|subtitles only", re.IGNORECASE)
_SEGUNDOS_CACHE_NYAA = 600
_cache_nyaa = {}


def _normalizar_titulo_busca(texto):
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


def _titulo_base_nyaa(titulo):
    """Título do DarkMahou sem "Nª Temporada" (o nyaa escreve "2nd Season"/"S2")."""
    return re.sub(r"\s*\d+ª?\s*Temporada\b", "", titulo, flags=re.IGNORECASE).strip(" -:")


def _episodio_do_nome_nyaa(nome):
    """Como _numero_episodio_no_nome_arquivo, aceitando o sufixo de versão ou
    de fim ("- 12 END [1080p]", "- 05v2 [")."""
    match = re.search(r"\bS\d{1,2}E(\d{1,4})\b", nome, re.IGNORECASE) or \
        re.search(r"\s-\s*(\d{1,4})(?:v\d+)?(?:\s+END)?\s*[\[(]", nome, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _temporada_do_nome_nyaa(nome):
    for padrao in (r"\bS(\d{1,2})E\d+", r"\b(\d+)(?:st|nd|rd|th)\s+Season\b", r"\bSeason\s+(\d+)\b", r"\bS(\d{1,2})\b"):
        match = re.search(padrao, nome, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 1


def _buscar_nyaa(consulta):
    """[(titulo, link .torrent, seeders)] da busca RSS do nyaa (categoria
    anime legendado em inglês, onde a Erai publica); [] em falha. Cache de
    10 min: a mesma busca roda pra cada checagem de travado."""
    em_cache = _cache_nyaa.get(consulta)
    if em_cache and time.time() - em_cache[0] < _SEGUNDOS_CACHE_NYAA:
        return em_cache[1]
    itens = []
    try:
        resp = _get_nyaa_com_espelho(_URL_RSS_NYAA, params={"page": "rss", "c": "1_2", "f": "0", "q": consulta})
        for item in ElementTree.fromstring(resp.content).iter("item"):
            itens.append((item.findtext("title", ""), item.findtext("link", ""),
                          int(item.findtext(f"{_NS_NYAA}seeders", "0") or 0)))
    except Exception as e:
        print(f" [SISTEMA] Busca no nyaa.si falhou ({consulta}): {e}")
    _cache_nyaa[consulta] = (time.time(), itens)
    return itens


def _opcoes_nyaa(registro, numero_episodio):
    """Opções (rotulo, link, None) do nyaa pro episódio numerado; especial fica de fora."""
    if _eh_especial(numero_episodio):
        return []
    titulo_base = _titulo_base_nyaa(registro["titulo"])
    palavras = _normalizar_titulo_busca(titulo_base).split()
    temporada = _detectar_numero_temporada(registro["titulo"])
    opcoes = []
    for nome, link, seeders in _buscar_nyaa(f"{_normalizar_titulo_busca(titulo_base)} {int(numero_episodio):02d}"):
        nome_normalizado = _normalizar_titulo_busca(nome).split()
        if (seeders < 1 or not link.endswith(".torrent") or _PADRAO_NYAA_DESCARTAR.search(nome)
                or not _PADRAO_LEGENDA_PT_BR.search(nome)
                or not all(p in nome_normalizado for p in palavras)
                or _episodio_do_nome_nyaa(nome) != int(numero_episodio)
                or _temporada_do_nome_nyaa(nome) != temporada):
            continue
        opcoes.append((f"nyaa ({seeders} seeders): {nome}", link, None))
    return opcoes


def _episodios_em_zips_yandex(soup):
    """{numero: [(rotulo, link_sintético, None)]} de todos os blocos da página."""
    episodios = {}
    for bloco in soup.find_all("div", class_="soraddl"):
        for numero, rotulo, link in _opcoes_zip_yandex_do_bloco(bloco):
            episodios.setdefault(numero, []).append((rotulo, link, None))
    return episodios


# ======================================================
# ✨ EPISÓDIO ESPECIAL (bloco "Episódio Especial", sem número)
# ======================================================
# 🔥 2026-09-24, pedido do usuário (caso real Kawaii dake ja Nai
# Shikimori-san, especial entre os episódios 6 e 7): o especial é
# identificado como "especial-K" (K = ordem do especial na página) em
# `downloads_em_andamento` e nos dicts de auditoria, e o estado fica em
# `registro["especiais"][K]` = {"apos": N, "status": ...} - FORA de
# `episodios`, cujas chaves a GAIA/IRIS convertem com int(). O nome final
# usa a posição: "Título - S01E06.5 - Especial 1.mkv".
# 🔥 2026-09-25, pedido do usuário ("pode tratar ep 0 como especial", caso
# real Boku no Kokoro no Yabai Yatsu 2ª Temporada): "Episódio 00" também é
# especial, antes do 1 ("S02E00.5 - Especial 1"), fora da contagem numerada.
_PREFIXO_ESPECIAL = "especial-"
_PADRAO_BLOCO_ESPECIAL = re.compile(rf"^{_PALAVRA_EPISODIO}\s+(?:Especial|0+)\b", re.IGNORECASE)
_PADRAO_NOME_ESPECIAL = re.compile(r" - (?:S\d+)?E\d+\.5 - Especial \d+")


def _eh_especial(ident):
    return str(ident).startswith(_PREFIXO_ESPECIAL)


def _ordem_especial(ident):
    return int(str(ident)[len(_PREFIXO_ESPECIAL):])


def _dados_especial(registro, ident):
    return registro.get("especiais", {}).get(str(_ordem_especial(ident)), {})


def _rotulo_episodio(registro, ident):
    """Como o episódio aparece em log/notificação: "6.5 - Especial 1" ou o número."""
    if not _eh_especial(ident):
        return str(ident)
    return f"{_dados_especial(registro, ident).get('apos', 0)}.5 - Especial {_ordem_especial(ident)}"


def _status_episodio(registro, ident):
    if _eh_especial(ident):
        return _dados_especial(registro, ident).get("status")
    return registro.get("episodios", {}).get(str(ident))


def _definir_status_episodio(registro, ident, status):
    if _eh_especial(ident):
        registro.setdefault("especiais", {}).setdefault(str(_ordem_especial(ident)), {})["status"] = status
    else:
        registro.setdefault("episodios", {})[str(ident)] = status


def _blocos_especiais(soup):
    """[(K, apos, bloco)] na ordem da página; `apos` = maior episódio numerado
    visto antes do bloco (a página nem sempre está em ordem)."""
    especiais, maior_antes = [], 0
    for bloco in soup.find_all("div", class_="soraddl"):
        titulo = bloco.find("h3")
        texto = titulo.get_text(strip=True) if titulo else ""
        if _PADRAO_BLOCO_ESPECIAL.match(texto):
            especiais.append((len(especiais) + 1, maior_antes, bloco))
            continue
        numero = _numero_do_bloco(texto)
        if numero is not None:
            maior_antes = max(maior_antes, numero)
    return especiais


def _especiais_a_baixar(chave, registro):
    """Registra os especiais da página (anime em andamento, ou 1ª vez para
    anime já completo) e devolve os "especial-K" ainda não tratados. Falha
    de rede não impede o resto da checagem."""
    if esta_completo(registro) and "especiais_verificado_em" in registro:
        conhecidos = registro.get("especiais", {})
    else:
        try:
            soup = BeautifulSoup(_obter_html_darkmahou(registro["url"]), "html.parser")
        except Exception as e:
            print(f" [SISTEMA] Erro ao procurar especiais de {registro['titulo']}: {e}")
            return []
        with lock_estado_animes:
            animes = _carregar_animes()
            if chave not in animes:
                return []
            salvo = animes[chave]
            conhecidos = salvo.setdefault("especiais", {})
            for ordem, apos, _ in _blocos_especiais(soup):
                conhecidos.setdefault(str(ordem), {"apos": apos})
            salvo["especiais_verificado_em"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _salvar_animes(animes)
        registro["especiais"] = conhecidos
        registro["especiais_verificado_em"] = salvo["especiais_verificado_em"]
    tratados = set()
    for campo in _CAMPOS_EPISODIO_TRATADO:
        tratados.update(str(k) for k in registro.get(campo, {}))
    pendentes = []
    for ordem, dados in sorted(conhecidos.items(), key=lambda par: int(par[0])):
        ident = f"{_PREFIXO_ESPECIAL}{ordem}"
        if not dados.get("status") and ident not in tratados:
            pendentes.append(ident)
    return pendentes


_PADRAO_INTERVALO_LOTE = re.compile(r"(\d{1,4})\s*[~-]\s*(\d{1,4})")


def _intervalo_do_lote(titulo_bloco):
    """(inicio, fim) de um bloco de lote ("Episódios 01~04 Sem Censura",
    "1ª Temporada Completa Legendado Torrent [01-12]"), ou None."""
    m = _PADRAO_INTERVALO_LOTE.search(titulo_bloco)
    if not m:
        return None
    inicio, fim = int(m.group(1)), int(m.group(2))
    return (inicio, fim) if inicio < fim else None


def _extrair_opcoes_download(url_anime, numero_episodio):
    """Baixa a página do anime e devolve [(rotulo, magnet, lote), ...] da
    PRIMEIRA linha (legendado) do bloco do episódio pedido. Lista vazia se a
    página/episódio não for encontrado - nunca lança exceção.

    🔥 Lotes sem censura (2026-09-24, pedido do usuário, caso real Haite
    Kudasai, Takamine-san: "Episódios 01~04 Sem Censura" num link só da
    WorldFansub): blocos com intervalo que cobre o episódio entram como
    opção com `lote = (inicio, fim)` - SÓ quando são sem censura, o único
    motivo pra preferir um lote a um avulso. O título do bloco vai no
    rótulo (a marcação "Sem Censura" às vezes só existe ali). Avulsos têm
    `lote = None`. _aplicar_selecao_lotes baixa só o arquivo do episódio
    dentro do lote."""
    try:
        html = _obter_html_darkmahou(url_anime)
    except Exception as e:
        print(f" [SISTEMA] Erro ao acessar página do anime ({url_anime}): {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    if _eh_especial(numero_episodio):
        for ordem, _, bloco in _blocos_especiais(soup):
            if ordem == _ordem_especial(numero_episodio):
                linha = bloco.find("tr")
                links = linha.find_all("a", href=_PADRAO_LINK_DOWNLOAD) if linha else []
                return [(a.get_text(strip=True), a["href"], None) for a in links]
        return []
    # 🔥 Prefixo, não igualdade exata (2026-08-05, bug real encontrado - o
    # último episódio de uma temporada às vezes vem com um sufixo, ex.:
    # "Episódio 13 Final" - igualdade exata contra "Episódio 13" nunca batia,
    # o download do episódio final simplesmente nunca era encontrado). `\b`
    # depois do número evita falso positivo de "Episódio 13" casar com
    # "Episódio 130" (ou similar).
    padrao_alvo = re.compile(rf"^{_PALAVRA_EPISODIO}\s+0*{numero_episodio}\b", re.IGNORECASE)
    avulsas, lotes, achou_avulso = [], [], False
    for bloco in soup.find_all("div", class_="soraddl"):
        titulo_bloco = bloco.find("h3")
        if not titulo_bloco:
            continue
        texto_bloco = titulo_bloco.get_text(strip=True)
        primeira_linha = bloco.find("tr")
        links_magnet = primeira_linha.find_all("a", href=_PADRAO_LINK_DOWNLOAD) if primeira_linha else []
        if padrao_alvo.match(texto_bloco) or (int(numero_episodio) == 1 and _PADRAO_BLOCO_FILME.match(texto_bloco)):
            if not achou_avulso:
                achou_avulso = True
                avulsas = [(a.get_text(strip=True), a["href"], None) for a in links_magnet]
            continue
        lote = _intervalo_do_lote(texto_bloco)
        if not lote or not lote[0] <= int(numero_episodio) <= lote[1]:
            continue
        for a in links_magnet:
            rotulo = f"{a.get_text(strip=True)} ({texto_bloco})"
            if _opcao_sem_censura(rotulo, a["href"]):
                lotes.append((rotulo, a["href"], lote))
    if not avulsas and not lotes:
        return _episodios_em_zips_yandex(soup).get(int(numero_episodio), [])
    return avulsas + lotes


# ======================================================
# ⬇️ DOWNLOAD (qBittorrent)
# ======================================================
def _sanitizar_nome_arquivo(nome):
    return "".join(c for c in nome if c.isalnum() or c in " -_()").strip() or "anime"


_ROMANOS_TEMPORADA = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}


def _detectar_numero_temporada(titulo):
    """Detecta o número da temporada a partir de como o DarkMahou/MAL escrevem
    no título (2026-08-03, pedido do usuário: incluir a temporada no nome do
    arquivo renomeado, ex. "S04E08" - também ajuda a reduzir o risco de
    colisão de nome entre temporadas documentado no docs/TODO.md). Reconhece "Nª
    Temporada"/"N Temporada" (padrão mais comum no DarkMahou), "Nth Season"/
    "Season N" (padrão do MAL, ver _titulo_para_busca_mal) e um algarismo
    romano (ex.: "Zhan Shen: Fanchen Shenyu II") - no FIM do título OU seguido
    de ":"/"-" (subtítulo depois da temporada, bug real encontrado 2026-08-04:
    "Mushoku Tensei III: Isekai Ittara Honki Dasu" saiu renomeado "S01" em vez
    de "S03" porque o "III" só era reconhecido se estivesse no fim exato da
    string). 1 (temporada única ou primeira) se nada bater - nunca lança erro."""
    match = re.search(r"(\d+)ª?\s*Temporada", titulo, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+Season\b", titulo, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\bSeason\s+(\d+)\b", titulo, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(I{1,3}|IV|V)\b(?=\s*[:\-]|\s*$)", titulo)
    if match:
        return _ROMANOS_TEMPORADA.get(match.group(1).upper(), 1)
    return 1


def qbittorrent_configurado():
    """True se há um host configurado (padrão localhost:8080, mesmo padrão de
    instalação do qBittorrent com Web UI habilitada em Ferramentas -> Opções ->
    Web UI) - não confirma que está de fato rodando/alcançável, só que não foi
    explicitamente desabilitado (QBITTORRENT_HOST="" no .env)."""
    return os.getenv("QBITTORRENT_HOST", "localhost") != ""


# 🔥 Abrir o qBittorrent sozinho se estiver fechado (2026-08-02, pergunta real do
# usuário - "o qBittorrent está fechado, a Gaia seria capaz de abrir ele?"). Caminho
# padrão de instalação (64 e 32 bits) - QBITTORRENT_EXECUTAVEL no .env sobrescreve
# se estiver instalado em outro lugar.
CAMINHOS_QBITTORRENT_PADRAO = [
    r"C:\Program Files\qBittorrent\qbittorrent.exe",
    r"C:\Program Files (x86)\qBittorrent\qbittorrent.exe",
]
_SEGUNDOS_ESPERA_ABRIR_QBITTORRENT = 30


def _localizar_executavel_qbittorrent():
    caminho_configurado = os.getenv("QBITTORRENT_EXECUTAVEL", "")
    if caminho_configurado and os.path.exists(caminho_configurado):
        return caminho_configurado
    for caminho in CAMINHOS_QBITTORRENT_PADRAO:
        if os.path.exists(caminho):
            return caminho
    return None


def _web_ui_qbittorrent_respondendo(host, porta):
    try:
        requests.get(f"http://{host}:{porta}", timeout=2)
        return True
    except requests.exceptions.ConnectionError:
        return False
    except Exception:
        return True  # respondeu algo (erro HTTP etc.) - já está de pé, só não deu 200


def _garantir_qbittorrent_rodando(host, porta):
    """Se a Web UI já responde, não faz nada. Se não (app fechado - conexão
    recusada, diferente de usuário/senha errados), tenta abrir o executável
    (caminho padrão de instalação ou QBITTORRENT_EXECUTAVEL) e espera até
    `_SEGUNDOS_ESPERA_ABRIR_QBITTORRENT` a Web UI subir antes de desistir. Devolve
    True se, ao final, a Web UI está alcançável (já estava, ou acabou de subir)."""
    if _web_ui_qbittorrent_respondendo(host, porta):
        return True

    caminho_exe = _localizar_executavel_qbittorrent()
    if not caminho_exe:
        print(" [SISTEMA] 🎬 qBittorrent não está rodando e não encontrei o executável instalado (defina QBITTORRENT_EXECUTAVEL no .env se estiver em outro lugar).")
        return False

    print(" [SISTEMA] 🎬 qBittorrent não estava aberto - abrindo automaticamente...")
    try:
        subprocess.Popen([caminho_exe])
    except Exception as e:
        print(f" [SISTEMA] Erro ao abrir o qBittorrent: {e}")
        return False

    for _ in range(_SEGUNDOS_ESPERA_ABRIR_QBITTORRENT):
        time.sleep(1)
        if _web_ui_qbittorrent_respondendo(host, porta):
            print(" [SISTEMA] 🎬 qBittorrent aberto e Web UI respondendo.")
            return True
    print(" [SISTEMA] qBittorrent foi aberto, mas a Web UI não respondeu a tempo (pode só estar demorando mais pra carregar).")
    return False


def _cliente_qbittorrent():
    """Import tardio (2026-08-02) - qbittorrent-api é uma dependência opcional desta
    feature só; import no topo do módulo derrubaria a feature inteira (e o boot do
    run.py) se não estiver instalado, mesmo pra quem não usa Modo Torrent nenhum."""
    import qbittorrentapi
    host = os.getenv("QBITTORRENT_HOST", "localhost")
    porta = int(os.getenv("QBITTORRENT_PORT", "8080") or "8080")
    _garantir_qbittorrent_rodando(host, porta)
    cliente = qbittorrentapi.Client(
        host=host, port=porta,
        username=os.getenv("QBITTORRENT_USUARIO", ""),
        password=os.getenv("QBITTORRENT_SENHA", ""),
    )
    cliente.auth_log_in()
    return cliente


def _hash_do_magnet(magnet):
    match_hash = re.search(r"btih:([a-fA-F0-9]{40})", magnet or "")
    return match_hash.group(1).lower() if match_hash else None


def _registrar_falha_download(chave, numero_episodio, motivo):
    """Guarda a 1ª vez que um episódio falhou (e o motivo mais recente) em
    `episodios_falha_download` - 2026-09-24, base do alerta de falha
    persistente (_coletar_alertas_falha_persistente). A entrada some quando o
    download finalmente dispara (baixar_episodio)."""
    animes = _carregar_animes()
    if chave not in animes:
        return
    falhas = animes[chave].setdefault("episodios_falha_download", {})
    entrada = falhas.setdefault(str(numero_episodio), {
        "desde": datetime.now().strftime("%Y-%m-%d %H:%M"), "alertado": False,
    })
    entrada["motivo"] = motivo
    _salvar_animes(animes)


def _adicionar_torrent(cliente, link, pasta_destino, parar_no_metadado=False):
    """Manda magnet ou .torrent pro qBittorrent. `parar_no_metadado`: para ao
    receber a lista de arquivos (lote/pacote), antes de baixar qualquer coisa."""
    extra = {"stop_condition": "MetadataReceived"} if parar_no_metadado else {}
    if link.startswith("magnet:"):
        extra["urls"] = link
    else:
        # Mesmos bytes de que saiu o hash - o qBittorrent não precisa
        # buscar o nyaa.si de novo.
        extra["torrent_files"] = _obter_torrent(link)[0]
    cliente.torrents_add(save_path=pasta_destino, category=CATEGORIA_QBITTORRENT, **extra)


def _opcoes_nao_tentadas(registro, numero_episodio, excluir=()):
    """(total de opções na página, opções ainda utilizáveis). 🔥 2026-09-24:
    magnet já tentado e dado como travado (_tratar_downloads_travados) fica
    de fora - senão o mesmo torrent morto seria escolhido de novo - e
    .torrent que não baixa (removido do nyaa.si) não é opção. `excluir`:
    hashes a mais (o torrent travado que ainda está no qBittorrent). Sem
    opção utilizável na página, entra a busca no nyaa (_opcoes_nyaa)."""
    tentados = set(registro.get("episodios_magnets_tentados", {}).get(str(numero_episodio), [])) | set(excluir)

    def _utilizaveis(opcoes):
        return [o for o in opcoes
                if (o[1].startswith("magnet:") or _obter_torrent(o[1]) is not None)
                and _hash_da_opcao(o[1]) not in tentados]

    opcoes = _extrair_opcoes_download(registro["url"], numero_episodio)
    utilizaveis = _utilizaveis(opcoes)
    if not utilizaveis:
        extras = _opcoes_nyaa(registro, numero_episodio)
        opcoes, utilizaveis = opcoes + extras, _utilizaveis(extras)
    return len(opcoes), utilizaveis


def baixar_episodio(chave, registro, numero_episodio, rebaixar_sem_censura=False):
    """Extrai o magnet do episódio (sem censura e 1080p HEVC de preferência) e manda pro
    qBittorrent - `save_path` é a pasta de downloads configurada no Painel
    (obter_anime_pasta_downloads, default "E:\\Downloads" - a mesma pasta onde o
    usuário já mantém episódio baixado e ainda não assistido, sem subpasta por
    anime: o nome do arquivo já sai único via _sanitizar_nome_arquivo/renomeação em
    verificar_downloads_em_andamento). Registra o hash em `downloads_em_andamento`
    do próprio anime, pra verificar_downloads_em_andamento() saber o que
    acompanhar. Não faz nada (devolve False) se o episódio já foi baixado (ou
    assistido), já está baixando, ou se não achou nenhum magnet - idempotente,
    seguro de chamar toda vez que processar_downloads_pendentes rodar.

    `rebaixar_sem_censura` (2026-09-24, exceção pedida pelo usuário pra
    Haite Kudasai, Takamine-san, baixado com censura antes da prioridade
    existir): baixa de novo um episódio já "baixado"/"assistido", mas só se
    a melhor opção for sem censura. O arquivo novo sai com sufixo
    SUFIXO_SEM_CENSURA e o antigo fica intocado.

    `numero_episodio` também aceita "especial-K" (ver _eh_especial)."""
    if _status_episodio(registro, numero_episodio) in ("baixado", "assistido") and not rebaixar_sem_censura:
        return False
    if str(numero_episodio) in registro.get("downloads_em_andamento", {}):
        return False

    total_opcoes, opcoes = _opcoes_nao_tentadas(registro, numero_episodio)
    rotulo_ep = _rotulo_episodio(registro, numero_episodio)
    magnet = _escolher_melhor_magnet(opcoes, None if _eh_especial(numero_episodio) else numero_episodio)
    escolhida = next((o for o in opcoes if o[1] == magnet), None)
    if rebaixar_sem_censura and not (escolhida and _opcao_sem_censura(escolhida[0], magnet)):
        print(f" [SISTEMA] {registro['titulo']} Episódio {rotulo_ep}: sem opção sem censura, não vou baixar de novo.")
        return False
    if not magnet:
        motivo = "todos os magnets disponíveis já travaram" if total_opcoes else "nenhum magnet na página do anime"
        print(f" [SISTEMA] Nenhum magnet encontrado pra {registro['titulo']} Episódio {rotulo_ep} ({motivo}).")
        _registrar_falha_download(chave, numero_episodio, motivo)
        return False

    hash_torrent = _hash_da_opcao(magnet)
    if not hash_torrent:
        print(f" [SISTEMA] Magnet de {registro['titulo']} Episódio {rotulo_ep} sem hash reconhecível - pulando.")
        _registrar_falha_download(chave, numero_episodio, "magnet sem hash reconhecível")
        return False

    pasta_destino = obter_anime_pasta_downloads()
    os.makedirs(pasta_destino, exist_ok=True)
    lote = escolhida[2] if escolhida and len(escolhida) > 2 else None
    # Lote já acompanhado por outro episódio deste anime: o mesmo torrent
    # serve, _aplicar_selecao_lotes só liga o arquivo deste episódio.
    lote_ja_no_cliente = bool(lote) and any(
        info.get("hash") == hash_torrent for info in registro.get("downloads_em_andamento", {}).values())
    import qbittorrentapi
    try:
        cliente = _cliente_qbittorrent()
        if not lote_ja_no_cliente:
            # Lote para ao receber o metadado: nada baixa antes de
            # _aplicar_selecao_lotes desligar os episódios não pedidos.
            _adicionar_torrent(cliente, magnet, pasta_destino, parar_no_metadado=bool(lote))
    except qbittorrentapi.Conflict409Error:
        # 🔥 2026-08-04, bug real reportado - "por que Black Torch não baixou?":
        # 409 Conflict do qBittorrent significa que esse HASH já existe no
        # cliente (o usuário baixa muito anime por fora do fluxo automático,
        # ver os outros torrents "[Judas]"/"[Erai-raws]" sem categoria
        # "gaia-animes") - normalmente porque já baixou esse episódio manualmente
        # antes de marcar "tenho interesse" aqui. Sem tratar isso, o episódio
        # ficava tentando (e falhando) de novo TODO dia pra sempre, já que
        # `episodios` nunca era atualizado. Marca um status sentinela (nem
        # "baixado" nem "assistido" - não dá pra confirmar que o arquivo ainda
        # existe onde o qBittorrent original apontava, só que JÁ EXISTIA) só
        # pra parar de tentar de novo - conta pra `_episodios_a_baixar` (fecha
        # o gap) mas NÃO conta pra `obter_ultimos_episodios_por_status`
        # (não afirma falsamente que está baixado/assistido).
        print(f" [SISTEMA] 🎬 {registro['titulo']} Episódio {rotulo_ep} já existe no qBittorrent (hash conhecido - provavelmente baixado por fora do fluxo automático) - não vou tentar de novo. Confira manualmente se o arquivo está na sua biblioteca.")
        animes = _carregar_animes()
        if chave in animes:
            _definir_status_episodio(animes[chave], numero_episodio, "conflito_qbittorrent")
            _salvar_animes(animes)
        return False
    except Exception as e:
        print(f" [SISTEMA] Erro ao mandar {registro['titulo']} Episódio {rotulo_ep} pro qBittorrent: {e}")
        _registrar_falha_download(chave, numero_episodio, f"erro no qBittorrent: {e}")
        return False

    animes = _carregar_animes()
    if chave in animes:
        agora = datetime.now().strftime("%Y-%m-%d %H:%M")
        info_download = {
            "hash": hash_torrent, "pasta": pasta_destino,
            # 🔥 2026-09-24: base pra detectar download travado
            # (_tratar_downloads_travados) - atualizado a cada avanço real.
            "progresso": 0.0, "progresso_em": agora,
            # Marca o nome final com SUFIXO_SEM_CENSURA na renomeação.
            "sem_censura": bool(escolhida and _opcao_sem_censura(escolhida[0], magnet)),
        }
        if lote:
            info_download["lote"] = list(lote)
        if rebaixar_sem_censura:
            info_download["rebaixar_sem_censura"] = True
        animes[chave].setdefault("downloads_em_andamento", {})[str(numero_episodio)] = info_download
        animes[chave].setdefault("episodios_download_disparado_em", {})[str(numero_episodio)] = f"{agora} ({hash_torrent})"
        animes[chave].get("episodios_falha_download", {}).pop(str(numero_episodio), None)
        _salvar_animes(animes)
    print(f" [SISTEMA] 🎬 Baixando {registro['titulo']} Episódio {rotulo_ep} ({escolhida[0] if escolhida else '?'})...")
    return True


def _episodios_a_baixar(registro):
    """Quais números de episódio tentar baixar pra esse anime - FECHA O GAP entre
    o maior episódio já conhecido (baixado, baixando, ou assistido) e o último
    lançado, em vez de baixar só "o último" (2026-08-02, pergunta real do usuário
    - "pegar só Últimos Lançamentos é suficiente? Não corro risco de perder algum
    ep?"). Risco real: "Últimos Lançamentos" é uma janela ROTATIVA de ~20 vagas na
    home do site - se 2 episódios de um mesmo anime saírem entre 2 checagens
    diárias (ou a Galateia ficar desligada num dia), ou se um anime sumir da
    lista por alguns dias (outros 20+ atualizando na frente dele) e reaparecer
    depois com o episódio pulado, o número do meio nunca seria detectado se só
    olhássemos "o último visto". Fechar o gap resolve os dois casos.

    Sem NADA conhecido ainda (acabou de marcar "tenho_interesse", ou de
    adicionar por link) - FAZ backfill do catálogo inteiro, do episódio 1 até
    o último lançado (2026-08-05, decisão explícita do usuário depois de um
    caso real: marcou interesse num anime que nunca tinha visto e só o
    episódio mais recente baixou, deixando os anteriores de fora - "eu não vi
    episódio nenhum". Antes disso baixava só o último, pra evitar redownload
    de quem já tinha assistido em outro lugar - o usuário decidiu que o
    padrão certo é presumir que quer assistir desde o início).

    🔥 2026-09-24, pedido do usuário ("mantenha um registro dos episódios
    baixados p evitar o problema do ep no meio"): antes partia do MAIOR
    conhecido - se o E17 falhasse (sem magnet ainda) e o E18 baixasse, o E17
    nunca mais era tentado. Agora devolve TODO número entre o MENOR conhecido
    e o último lançado que não aparece em nenhum registro
    (_episodios_ja_tratados) - buraco no meio é retentado a cada checagem até
    baixar. Começar do menor (e não do 1) preserva quem começou a acompanhar
    no meio da temporada."""
    ultimo_lancado = registro.get("ultimo_episodio_visto")
    if ultimo_lancado is None:
        return []
    tratados = _episodios_ja_tratados(registro)
    if not tratados:
        return list(range(1, ultimo_lancado + 1))
    return [n for n in range(min(tratados), ultimo_lancado + 1) if n not in tratados]


# Todo campo por episódio que prova que ele já foi baixado/tratado alguma vez.
# `episodios` sozinho não serve: sincronizar_biblioteca_local REMOVE o
# "baixado" quando o usuário apaga o arquivo sem mover pra assistidos, e aí
# o episódio pareceria "nunca baixado" e seria baixado de novo. Os dicts
# "_em" são auditoria e nunca perdem entrada.
_CAMPOS_EPISODIO_TRATADO = (
    "episodios", "downloads_em_andamento", "episodios_download_disparado_em",
    "episodios_baixado_em", "episodios_assistido_em", "episodios_renomeado_em",
    "episodios_revertido_em", "episodios_removido_qbittorrent_em", "episodios_erro_renomear",
)


def _episodios_ja_tratados(registro):
    """Números de episódio que já foram baixados, estão baixando, foram
    assistidos ou apagados pelo usuário - nenhum deles deve ser baixado de
    novo. `episodios_download_disparado_em` (2026-09-24) é o registro
    permanente de todo download disparado por baixar_episodio."""
    numeros = set()
    for campo in _CAMPOS_EPISODIO_TRATADO:
        numeros.update(int(n) for n in registro.get(campo, {}) if str(n).isdigit())
    return numeros


# ======================================================
# 📦 PÁGINA SÓ COM PACOTE DA TEMPORADA (sem bloco "Episódio N")
# ======================================================
# 🔥 2026-09-24, pedido do usuário (casos reais Kimi no Koto ga
# Daidaidaidaidaisuki na 100-nin no Kanojo e Hitsugi no Chaika: Avenging
# Battle): a página tem só um bloco com o nome do anime / "2ª Temporada BD
# Completo". Sem número no título, a lista de episódios só existe dentro do
# torrent: o pacote é adicionado parado (`registro["pacote_completo"]`) e,
# quando o metadado chega, _expandir_pacotes_completos vira cada vídeo num
# episódio acompanhado como lote. Daí em diante vale o fluxo de lote
# (seleção, renomeação, raiz da pasta, remoção no último).


def _opcoes_pacote_completo(soup):
    """Opções dos blocos da página quando ela não tem NENHUM "Episódio N"
    (especial fica de fora). Lista vazia em página normal."""
    blocos = soup.find_all("div", class_="soraddl")
    textos = [b.find("h3").get_text(strip=True) if b.find("h3") else "" for b in blocos]
    if any(_numero_do_bloco(t) is not None for t in textos):
        return []
    opcoes = []
    for bloco, texto in zip(blocos, textos):
        if _PADRAO_BLOCO_ESPECIAL.match(texto):
            continue
        linha = bloco.find("tr")
        for a in (linha.find_all("a", href=_PADRAO_LINK_DOWNLOAD) if linha else []):
            opcoes.append((f"{a.get_text(strip=True)} ({texto})", a["href"], None))
    return opcoes


def _baixar_pacote_completo(chave, registro):
    """Dispara o pacote da temporada de um anime sem "Episódio N" na página.
    Devolve True se adicionou. Só roda sem `ultimo_episodio_visto` (página
    normal sempre tem) e sem pacote já em andamento."""
    if registro.get("ultimo_episodio_visto") is not None or registro.get("pacote_completo"):
        return False
    try:
        soup = BeautifulSoup(_obter_html_darkmahou(registro["url"]), "html.parser")
    except Exception as e:
        print(f" [SISTEMA] Erro ao consultar página de {registro['titulo']}: {e}")
        return False
    tentados = set(registro.get("pacote_completo_tentados", []))
    opcoes = [o for o in _opcoes_pacote_completo(soup)
              if (o[1].startswith("magnet:") or _obter_torrent(o[1]) is not None)
              and _hash_da_opcao(o[1]) not in tentados]
    link = _escolher_melhor_magnet(opcoes)
    hash_torrent = _hash_da_opcao(link) if link else None
    if not hash_torrent:
        return False
    rotulo = next(o[0] for o in opcoes if o[1] == link)
    try:
        _adicionar_torrent(_cliente_qbittorrent(), link, obter_anime_pasta_downloads(), parar_no_metadado=True)
    except Exception as e:
        print(f" [SISTEMA] Erro ao mandar o pacote de {registro['titulo']} pro qBittorrent: {e}")
        return False
    with lock_estado_animes:
        animes = _carregar_animes()
        if chave in animes:
            animes[chave]["pacote_completo"] = {
                "hash": hash_torrent, "rotulo": rotulo, "aguardando_arquivos": True,
                "desde": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "sem_censura": _opcao_sem_censura(rotulo, link),
            }
            _salvar_animes(animes)
    print(f" [SISTEMA] 🎬 Baixando pacote da temporada de {registro['titulo']} ({rotulo}) - episódios saem da lista de arquivos do torrent.")
    return True


def _expandir_pacotes_completos(cliente, animes):
    """Pacote com metadado recebido: cada vídeo com número no nome vira
    episódio em `downloads_em_andamento` (como lote), só os ainda não
    tratados. Sem metadado por `anime_download_travado_horas`, o pacote sai
    e a próxima checagem tenta outra opção. Devolve True se mudou estado."""
    mudou = False
    agora = datetime.now()
    limite = timedelta(hours=obter_anime_download_travado_horas())
    for chave, registro in animes.items():
        pacote = registro.get("pacote_completo")
        if not pacote or not pacote.get("aguardando_arquivos"):
            continue
        try:
            arquivos = cliente.torrents_files(torrent_hash=pacote["hash"])
        except Exception as e:
            print(f" [SISTEMA] Erro ao listar arquivos do pacote de {registro['titulo']}: {e}")
            continue
        numeros = sorted({
            n for n in (_numero_episodio_no_nome_arquivo(f.name) for f in arquivos
                        if f.name.lower().endswith(EXTENSOES_VIDEO))
            if n is not None
        })
        if not numeros:
            sem_metadado = not arquivos
            if sem_metadado and agora - datetime.strptime(pacote["desde"], "%Y-%m-%d %H:%M") < limite:
                continue  # metadado ainda não chegou
            motivo = "sem metadado" if sem_metadado else "nenhum vídeo com número de episódio no nome"
            print(f" [SISTEMA] ⚠️ Pacote de {registro['titulo']} descartado ({motivo}) - a próxima checagem tenta outra opção.")
            try:
                cliente.torrents_delete(delete_files=False, torrent_hashes=pacote["hash"])
            except Exception as e:
                print(f" [SISTEMA] Erro ao remover pacote {pacote['hash']}: {e}")
            registro.setdefault("pacote_completo_tentados", []).append(pacote["hash"])
            registro.pop("pacote_completo")
            mudou = True
            continue
        carimbo = agora.strftime("%Y-%m-%d %H:%M")
        tratados = _episodios_ja_tratados(registro)
        novos = [n for n in numeros if n not in tratados]
        for n in novos:
            registro.setdefault("downloads_em_andamento", {})[str(n)] = {
                "hash": pacote["hash"], "pasta": obter_anime_pasta_downloads(),
                "progresso": 0.0, "progresso_em": carimbo,
                "sem_censura": pacote.get("sem_censura", False), "lote": [numeros[0], numeros[-1]],
            }
            registro.setdefault("episodios_download_disparado_em", {})[str(n)] = f"{carimbo} ({pacote['hash']}, pacote)"
        registro["ultimo_episodio_visto"] = max(numeros[-1], registro.get("ultimo_episodio_visto") or 0)
        pacote["aguardando_arquivos"] = False
        pacote["episodios"] = numeros
        if not novos:
            try:
                cliente.torrents_delete(delete_files=False, torrent_hashes=pacote["hash"])
            except Exception as e:
                print(f" [SISTEMA] Erro ao remover pacote {pacote['hash']}: {e}")
        print(f" [SISTEMA] 🎬 Pacote de {registro['titulo']}: {len(numeros)} episódios no torrent, {len(novos)} a baixar.")
        mudou = True
    return mudou


def _baixar_pendentes_do_registro(chave, registro):
    """Baixa (fechando o gap, ver _episodios_a_baixar) todo episódio pendente
    de UM anime específico - usado tanto pelo loop diário
    (processar_downloads_pendentes, todos os "tenho_interesse") quanto pelo
    disparo imediato ao marcar interesse (baixar_pendentes_de, só esse
    anime). Devolve (quantos downloads novos foram disparados, lista dos
    números de episódio disparados de verdade) - a lista existe pra
    `processar_downloads_pendentes` conseguir montar a notificação "começou a
    baixar" com título+episódio (2026-09-05, pedido do usuário), não só o
    contador de sempre. O 3º item (números que NÃO dispararam - sem magnet,
    erro no qBittorrent etc.) vai pro histórico de checagens (2026-09-24)."""
    numeros_disparados, numeros_falhos = [], []
    for n in _episodios_a_baixar(registro):
        (numeros_disparados if baixar_episodio(chave, registro, n) else numeros_falhos).append(n)
    # Especiais vão com o rótulo ("6.5 - Especial 1") pra notificação/histórico.
    for ident in _especiais_a_baixar(chave, registro):
        disparou = baixar_episodio(chave, registro, ident)
        (numeros_disparados if disparou else numeros_falhos).append(_rotulo_episodio(registro, ident))
    if _baixar_pacote_completo(chave, registro):
        numeros_disparados.append("pacote da temporada")
    return len(numeros_disparados), numeros_disparados, numeros_falhos


def processar_downloads_pendentes():
    """Pros animes marcados "tenho_interesse", baixa qualquer episódio entre o
    maior já conhecido e o último lançado que ainda não foi baixado nem está
    baixando (ver _episodios_a_baixar - fecha gaps, não só "o último").
    Devolve (quantos downloads novos foram disparados, [(titulo, numero), ...]
    de cada um) - a lista de itens é o que permite notificar QUAIS animes
    começaram a baixar (ver `formatar_texto_download_iniciado`), em vez de só
    um contador solto no log - e [(titulo, numero), ...] dos que falharam."""
    if not qbittorrent_configurado():
        return 0, [], []
    disparados = 0
    itens = []
    falhas = []
    for chave, registro in _carregar_animes().items():
        if registro.get("interesse") != "tenho_interesse":
            continue
        qtd, numeros, numeros_falhos = _baixar_pendentes_do_registro(chave, registro)
        disparados += qtd
        itens.extend((registro["titulo"], numero) for numero in numeros)
        falhas.extend((registro["titulo"], numero) for numero in numeros_falhos)
    return disparados, itens, falhas


def formatar_texto_download_iniciado(itens):
    """Texto pronto pra notificar (Discord/log) quando um episódio novo
    começa a baixar automaticamente - None se nada foi disparado nessa
    checagem (mesmo padrão de silêncio de `formatar_texto_pendentes`).

    🔥 Pedido do usuário (2026-09-05): "quero ser notificado quando começa a
    baixar algum episodio" - antes disso, `executar_checagem_completa` só
    devolvia um CONTADOR (`disparados`) que a GAIA imprimia no log e nunca
    mandava pro Discord - o usuário só descobria que baixou algo abrindo o
    Painel."""
    if not itens:
        return None
    linhas = [f"- {titulo} - Episódio {numero}" for titulo, numero in itens]
    return "⬇ Começou a baixar:\n" + "\n".join(linhas)


def baixar_pendentes_de(chave):
    """Dispara o download dos episódios pendentes de UM anime específico, sem
    esperar o loop diário (2026-08-04, pedido do usuário: "gosto de baixar
    episódios na hora" - marcar "tenho interesse" no Painel já tenta baixar na
    hora, em vez de só no próximo ciclo de processar_downloads_pendentes,
    até 24h depois). Chamado pelo Painel (ui/qt_modais/animes.py) numa thread
    separada (faz rede - scraping da página + qBittorrent), nunca a própria
    thread da GUI. Devolve quantos downloads novos foram disparados - 0 se o
    qBittorrent não estiver configurado, o anime não existir, ou não estiver
    marcado "tenho_interesse" (proteção contra corrida - o usuário pode ter
    trocado pra "sem_interesse" de novo antes da thread rodar)."""
    if not qbittorrent_configurado():
        return 0
    registro = _carregar_animes().get(chave)
    if not registro or registro.get("interesse") != "tenho_interesse":
        return 0
    disparados, _numeros, _falhos = _baixar_pendentes_do_registro(chave, registro)
    return disparados


def baixar_pendentes_com_aviso(chave):
    """baixar_pendentes_de + o aviso de avisar_download_manual - usado pela
    ponte HTTP (`/anime/baixar_pendentes`), que o Painel/`/adicionar_anime`
    chamam logo depois de adicionar um anime por link."""
    if not qbittorrent_configurado():
        return 0, _AVISO_SEM_QBITTORRENT
    registro = _carregar_animes().get(chave)
    if not registro or registro.get("interesse") != "tenho_interesse":
        return 0, None
    disparados, _numeros, falhos = _baixar_pendentes_do_registro(chave, registro)
    return disparados, avisar_download_manual(chave, disparados, falhos)


def baixar_episodios_selecionados(chave, numeros):
    """Baixa só os números de episódio EXPLICITAMENTE escolhidos (2026-08-07,
    modal de seleção ao adicionar um anime manualmente por link - Assistente de
    Animes ou "🎬 Adicionar Anime" do Menu Radial) - ao contrário de
    _baixar_pendentes_do_registro (que fecha o gap inteiro sozinho, sem
    perguntar), aqui quem decide quais números baixar é quem chama (o usuário,
    via checkboxes no `ModalSelecionarEpisodios`). Mesma proteção de corrida de
    baixar_pendentes_de - confere `tenho_interesse` de novo antes de baixar."""
    if not qbittorrent_configurado():
        return 0
    registro = _carregar_animes().get(chave)
    if not registro or registro.get("interesse") != "tenho_interesse":
        return 0
    return baixar_episodios_selecionados_com_aviso(chave, numeros)[0]


def baixar_episodios_selecionados_com_aviso(chave, numeros):
    """baixar_episodios_selecionados + o aviso de avisar_download_manual."""
    if not qbittorrent_configurado():
        return 0, _AVISO_SEM_QBITTORRENT
    registro = _carregar_animes().get(chave)
    if not registro or registro.get("interesse") != "tenho_interesse":
        return 0, None
    disparados, falhos = 0, []
    for numero_episodio in numeros:
        if baixar_episodio(chave, registro, numero_episodio):
            disparados += 1
        else:
            falhos.append(numero_episodio)
    return disparados, avisar_download_manual(chave, disparados, falhos)


# ======================================================
# 💬 AVISO DO DOWNLOAD MANUAL (adicionar por link)
# ======================================================
# 🔥 2026-09-25, pedido do usuário ("moirai podia mandar um aviso qnd eu
# tento baixar um anime manualmente passando link, msm qnd n encontra nada",
# caso real Boku no Kokoro no Yabai Yatsu 2ª Temporada): a página só tinha
# "Episódio 00" e um bloco "Temporada Completa" com Yandex/Jottacloud - o
# Painel não mostrava nada e o usuário não sabia o motivo.
_AVISO_SEM_QBITTORRENT = "O qBittorrent não está configurado no MOIRAI - nada foi mandado pra baixar."


def _blocos_so_link_direto(soup):
    """Títulos dos blocos da página que têm link de download, mas nenhum
    magnet/.torrent (Google Drive, sync.com...) nem zip de .torrent no
    Yandex - o MOIRAI não consegue baixar por eles."""
    titulos = []
    for bloco in soup.find_all("div", class_="soraddl"):
        titulo = bloco.find("h3")
        links = [a["href"] for a in bloco.find_all("a", href=True) if not a["href"].startswith("#")]
        if (titulo and links and not any(_PADRAO_LINK_DOWNLOAD.search(h) for h in links)
                and not _opcoes_zip_yandex_do_bloco(bloco)):
            titulos.append(titulo.get_text(strip=True))
    return titulos


def avisar_download_manual(chave, disparados, falhos):
    """Texto explicando o que ficou de fora de um download pedido à mão, ou
    None se tudo que a página oferece foi mandado pro qBittorrent. `falhos`:
    episódios tentados sem sucesso (sem magnet, erro no qBittorrent)."""
    registro = _carregar_animes().get(chave)
    if not registro:
        return None
    avisos = []
    if falhos:
        rotulos = ", ".join(_rotulo_episodio(registro, f) for f in falhos)
        avisos.append(f"Não consegui baixar o(s) episódio(s) {rotulos} (sem magnet/torrent utilizável na página, ou erro no qBittorrent - detalhe no log do MOIRAI).")
    try:
        blocos_diretos = _blocos_so_link_direto(BeautifulSoup(_obter_html_darkmahou(registro["url"]), "html.parser"))
    except Exception:
        blocos_diretos = []
    if blocos_diretos:
        avisos.append(
            "A página só tem link de download direto (sem magnet/torrent) em: "
            + "; ".join(f"\"{t}\"" for t in blocos_diretos)
            + ". O MOIRAI só baixa por magnet/torrent - esse precisa ser baixado à mão."
        )
    if not disparados and not falhos:
        if registro.get("ultimo_episodio_visto") is None and not registro.get("pacote_completo") and not registro.get("especiais"):
            avisos.insert(0, "Não achei nenhum episódio com magnet/torrent na página do anime.")
        else:
            avisos.insert(0, "Nada novo pra baixar - os episódios da página já foram baixados antes ou estão baixando.")
    return "\n".join(avisos) or None


def _maior_arquivo_video(caminho):
    """Acha o maior arquivo de vídeo (EXTENSOES_VIDEO) dentro de `caminho` - que pode
    ser o próprio arquivo (torrent de episódio único) ou uma pasta (torrent com
    legendas/extras junto, ou um batch de vários episódios - nesse caso só o MAIOR
    arquivo é considerado "o episódio", os demais ficam como estavam)."""
    if os.path.isfile(caminho):
        return caminho if caminho.lower().endswith(EXTENSOES_VIDEO) else None
    candidatos = []
    for raiz, _, arquivos in os.walk(caminho):
        for nome in arquivos:
            if nome.lower().endswith(EXTENSOES_VIDEO):
                candidatos.append(os.path.join(raiz, nome))
    if not candidatos:
        return None
    return max(candidatos, key=os.path.getsize)


def _numero_episodio_no_nome_arquivo(caminho):
    """Extrai o episódio declarado no nome do vídeo, se ele for inequívoco.

    Isto é uma proteção de auditoria para ``content_path`` incorreto do
    qBittorrent: não se pode renomear um ``S01E20`` como se fosse o episódio
    22 apenas porque o hash que está sendo acompanhado era o do 22.
    """
    nome = os.path.basename(caminho)
    padrao = re.search(r"\bS\d{1,2}E(\d{1,4})\b", nome, re.IGNORECASE)
    if padrao:
        return int(padrao.group(1))
    padrao = re.search(r"-\s*(\d{1,4})\s*(?:\[|\(|_|\.|$)", nome)
    return int(padrao.group(1)) if padrao else None


def tem_episodio_disponivel_para_assistir(registro):
    """True se o anime tem pelo menos 1 episódio `"baixado"` (baixado, ainda
    NÃO `"assistido"`) - usado pela aba "▶️ Disponíveis" do Painel
    (ui/qt_modais/animes.py, 2026-08-14, pedido do usuário: "lista filtrada de
    animes aprovados que possuem episódios baixados e disponíveis para
    assistir")."""
    return any(status == "baixado" for status in registro.get("episodios", {}).values())


def obter_animes_com_download_ativo():
    """Lista de títulos com pelo menos 1 episódio em `downloads_em_andamento` agora -
    usada pelo Menu Radial (2026-08-07) pra mostrar a categoria contextual "⬇️
    Downloads Ativos" só quando ela tem conteúdo de verdade pra mostrar, e por
    `executar_checagem_completa` (ver `formatar_texto_baixando_agora` abaixo)."""
    animes = _carregar_animes()
    return [
        registro["titulo"]
        for registro in animes.values()
        if registro.get("downloads_em_andamento")
    ]


def formatar_texto_baixando_agora(titulos):
    """Texto pronto pra notificar - None se não há nenhum download em
    andamento agora (mesmo padrão de silêncio de `formatar_texto_pendentes`).

    🔥 Bug real reportado pelo usuário (2026-09-05): "ao clicar em verificar
    lançamentos, ele não lista os animes que estão sendo baixados" - o botão
    "🔄 Verificar agora" (ui/qt_modais/animes.py) só mostrava o total de
    downloads DISPARADOS nessa checagem (`disparados`), nunca os que já
    estavam baixando de checagens anteriores (torrent lento, por exemplo).
    Ver `executar_checagem_completa`, que passa `obter_animes_com_download_ativo()`
    pra cá."""
    if not titulos:
        return None
    return "⬇ Baixando agora:\n" + "\n".join(f"- {titulo}" for titulo in titulos)


def verificar_downloads_em_andamento():
    """Roda mais frequente que a checagem diária (loop próprio, minutos - ver
    _monitorar_downloads_animes_loop, run.py). Consulta o qBittorrent por CADA hash
    em `downloads_em_andamento` de cada anime; quando `progress >= 1.0` (concluído,
    incluindo quem já virou seed), renomeia o maior arquivo de vídeo baixado pro
    padrão "{Título} - E{NN}{extensão}" e marca o episódio como concluído (sai de
    `downloads_em_andamento`, `episodios[N] = "baixado"`). Devolve quantos
    episódios terminaram nessa checagem.

    🔥 Remove o torrent da LISTA do qBittorrent depois de renomeado (2026-08-14,
    bug real reportado pelo usuário: "o torrent continua no qBittorrent após o
    anime ser processado e renomeado") - mesmo raciocínio de
    `renomear_por_hash_qbittorrent(remover_da_lista_depois=True)`: a Gaia já
    tem tudo que precisa saber sobre esse episódio (arquivo renomeado, marcado
    "baixado") a partir daqui, não precisa que o qBittorrent continue
    rastreando/semeando esse torrent específico. `delete_files=False` sempre -
    nunca apaga o arquivo de verdade, só para de acompanhar aquele torrent.

    🔥 Registro de auditoria (2026-08-16, pedido do usuário depois de investigar
    um arquivo de anime que a Gaia baixou mas nunca renomeou, sem nenhum rastro
    do porquê) - cada transição de estado grava timestamp num dict próprio no
    registro do anime: `episodios_baixado_em` (progress virou 1.0),
    `episodios_renomeado_em` (arquivo renomeado com sucesso) OU
    `episodios_erro_renomear` (motivo de não ter renomeado - arquivo não
    encontrado, destino já existia, ou erro do SO) e
    `episodios_removido_qbittorrent_em`. Nenhum desses depende do arquivo ainda
    existir no disco (sobrevive a limpeza manual do usuário, ver
    `episodios_assistido_em` que já funcionava assim) - histórico persistente
    pra investigar qualquer gap futuro sem precisar reconstruir na mão."""
    if not qbittorrent_configurado():
        return 0
    animes = _carregar_animes()
    tem_pacote = any(r.get("pacote_completo", {}).get("aguardando_arquivos") for r in animes.values())
    if not tem_pacote and not any(r.get("downloads_em_andamento") for r in animes.values()):
        return 0

    try:
        cliente = _cliente_qbittorrent()
    except Exception as e:
        print(f" [SISTEMA] Erro ao conectar no qBittorrent pra checar downloads: {e}")
        return 0

    mudou = _expandir_pacotes_completos(cliente, animes) if tem_pacote else False
    pendentes = [
        (chave, str(ep), info)
        for chave, registro in animes.items()
        for ep, info in registro.get("downloads_em_andamento", {}).items()
    ]
    concluidos = 0
    mudou = _aplicar_selecao_lotes(cliente, animes) or mudou
    travados = []
    limite_travado = timedelta(hours=obter_anime_download_travado_horas())
    limite_sem_seed = min(limite_travado, timedelta(hours=_HORAS_TRAVADO_SEM_SEED))
    for chave, numero_episodio_str, info in pendentes:
        registro = animes[chave]
        status_atual = _status_episodio(registro, numero_episodio_str)
        if status_atual in ("baixado", "assistido") and not info.get("rebaixar_sem_censura"):
            # Um estado antigo pode sobreviver a uma conclusão anterior. Não o
            # deixe rebaixar um episódio assistido nem tocar em um torrent que
            # o usuário possa estar mantendo no qBittorrent por conta própria.
            registro.get("downloads_em_andamento", {}).pop(numero_episodio_str, None)
            print(f" [SISTEMA] ⚠️ {registro['titulo']} Episódio {numero_episodio_str}: acompanhamento antigo removido; o episódio já está marcado como {status_atual}.")
            mudou = True
            continue
        try:
            torrents = cliente.torrents_info(torrent_hashes=info["hash"])
        except Exception as e:
            print(f" [SISTEMA] Erro ao consultar torrent {info['hash']}: {e}")
            continue
        if not torrents or torrents[0].progress < 1.0:
            # 🔥 2026-09-24: download travado (torrent sem ninguém
            # compartilhando, ou removido do qBittorrent por fora) ficava
            # "baixando" pra sempre. Cada avanço real renova `progresso_em`;
            # sem avanço por `anime_download_travado_horas`, vai pra
            # _tratar_downloads_travados trocar pelo próximo magnet.
            progresso = torrents[0].progress if torrents else None
            if progresso is not None and progresso > info.get("progresso", 0.0):
                info["progresso"] = progresso
                info["progresso_em"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                mudou = True
            elif "progresso_em" not in info:
                # Download disparado antes desta mudança: começa a contar agora.
                info["progresso_em"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                info.setdefault("progresso", progresso or 0.0)
                mudou = True
            elif datetime.now() - datetime.strptime(info["progresso_em"], "%Y-%m-%d %H:%M") >= (
                    limite_sem_seed if torrents and getattr(torrents[0], "num_seeds", None) == 0 else limite_travado):
                travados.append((chave, numero_episodio_str, info["hash"], progresso))
            continue

        agora = datetime.now().strftime("%Y-%m-%d %H:%M")
        caminho_conteudo = torrents[0].content_path or info["pasta"]
        if info.get("lote"):
            # Lote: o maior vídeo da pasta pode ser outro episódio - vale o
            # arquivo do próprio torrent com o número deste episódio.
            arquivo_lote = _arquivo_do_episodio_no_lote(cliente, info["hash"], int(numero_episodio_str))
            arquivo_video = os.path.join(torrents[0].save_path, arquivo_lote.name) if arquivo_lote else None
        else:
            arquivo_video = _maior_arquivo_video(caminho_conteudo)
        if arquivo_video:
            # Especial não tem número próprio pra conferir no nome.
            episodio_no_arquivo = None if _eh_especial(numero_episodio_str) else _numero_episodio_no_nome_arquivo(arquivo_video)
            if episodio_no_arquivo is not None and episodio_no_arquivo != int(numero_episodio_str):
                if _arquivo_pertence_ao_torrent(cliente, info["hash"], arquivo_video):
                    # 🔥 2026-09-24 (Bleach E08 travado desde 23/09): fansub
                    # com numeração absoluta da série ("S17E48" pro E08 da
                    # temporada). O arquivo está DENTRO do torrent que o
                    # MOIRAI pediu pra esse episódio, então o conteúdo é o
                    # certo - só o número no nome difere. A trava continua
                    # valendo quando o arquivo NÃO pertence ao torrent (o
                    # caso real de 2026-09-07: `content_path` apontando pra
                    # outro arquivo).
                    registro.setdefault("episodios_numeracao_divergente_aceita", {})[numero_episodio_str] = (
                        f"{agora}: nome informa E{episodio_no_arquivo:02d}, mas o arquivo pertence ao torrent "
                        f"{info['hash']} pedido pra esse episódio ('{os.path.basename(arquivo_video)}')"
                    )
                    registro.get("episodios_erro_renomear", {}).pop(numero_episodio_str, None)
                    print(f" [SISTEMA] 🎬 {registro['titulo']} Episódio {numero_episodio_str}: arquivo com numeração absoluta (E{episodio_no_arquivo:02d}) aceito - pertence ao torrent pedido.")
                else:
                    detalhe = (
                        f"conteúdo do qBittorrent diverge do episódio acompanhado "
                        f"(esperado E{int(numero_episodio_str):02d}, nome informa E{episodio_no_arquivo:02d}: "
                        f"'{arquivo_video}'). Mantido em acompanhamento; não vou renomear nem marcar como baixado."
                    )
                    anterior = registro.get("episodios_erro_renomear", {}).get(numero_episodio_str, "")
                    # Só avisa quando o motivo muda - antes repetia no log a cada 5min.
                    if not anterior.endswith(detalhe):
                        registro.setdefault("episodios_erro_renomear", {})[numero_episodio_str] = f"{agora}: {detalhe}"
                        print(f" [SISTEMA] ⚠️ {registro['titulo']} Episódio {numero_episodio_str}: {detalhe}")
                        mudou = True
                    continue
            extensao = os.path.splitext(arquivo_video)[1]
            numero_temporada = _detectar_numero_temporada(registro["titulo"])
            sufixo = SUFIXO_SEM_CENSURA if info.get("sem_censura") else ""
            if _eh_especial(numero_episodio_str):
                parte_ep = f"E{_dados_especial(registro, numero_episodio_str).get('apos', 0):02d}.5 - Especial {_ordem_especial(numero_episodio_str)}"
            else:
                parte_ep = f"E{int(numero_episodio_str):02d}"
            nome_novo = f"{_sanitizar_nome_arquivo(registro['titulo'])} - S{numero_temporada:02d}{parte_ep}{sufixo}{extensao}"
            # Lote sai da subpasta do torrent e vai pra raiz da pasta de
            # downloads, junto dos outros episódios (2026-09-24, pedido do
            # usuário: "ficarem tudo em downloads com os outros").
            pasta_final = torrents[0].save_path if info.get("lote") else os.path.dirname(arquivo_video)
            caminho_novo = os.path.join(pasta_final, nome_novo)
            try:
                if not os.path.exists(caminho_novo):
                    if info.get("lote"):
                        # Torrent pode continuar ativo pros outros episódios
                        # do lote: renomeia pela API pra não virar missingFiles.
                        _renomear_arquivo_via_api_qbittorrent(
                            cliente, torrents[0], arquivo_video, nome_novo, na_raiz=True)
                    else:
                        os.rename(arquivo_video, caminho_novo)
                    registro.setdefault("episodios_renomeado_em", {})[numero_episodio_str] = agora
                else:
                    registro.setdefault("episodios_erro_renomear", {})[numero_episodio_str] = (
                        f"{agora}: destino já existia ({nome_novo}) - arquivo original não foi mexido"
                    )
                    print(f" [SISTEMA] ⚠️ {registro['titulo']} Episódio {numero_episodio_str}: destino já existe; mantendo o torrent em acompanhamento.")
                    mudou = True
                    continue
            except Exception as e:
                print(f" [SISTEMA] Erro ao renomear {arquivo_video}: {e}")
                registro.setdefault("episodios_erro_renomear", {})[numero_episodio_str] = f"{agora}: {e}"
                mudou = True
                continue
        else:
            # 🔥 Bug real reportado (2026-08-16, "por que não renomeou na hora?")
            # - até aqui o episódio era marcado "baixado" mesmo quando o
            # arquivo não foi achado (torrent com estado "missingFiles" no
            # qBittorrent, ou content_path apontando pra pasta vazia/errada) -
            # sem NENHUM registro do motivo, só descoberto meses depois
            # investigando um arquivo cru que nunca foi renomeado. Registro de
            # auditoria (pedido do usuário) - não impede marcar "baixado" (o
            # torrent JÁ terminou de baixar de verdade, só o passo de renomear
            # que falhou), mas deixa rastro do porquê pra próxima investigação.
            registro.setdefault("episodios_erro_renomear", {})[numero_episodio_str] = (
                f"{agora}: nenhum arquivo de vídeo encontrado em '{caminho_conteudo}' "
                f"(qBittorrent pode estar reportando content_path desatualizado/'missingFiles')"
            )
            print(f" [SISTEMA] ⚠️ {registro['titulo']} Episódio {numero_episodio_str}: arquivo de vídeo não encontrado; mantendo o torrent em acompanhamento.")
            mudou = True
            continue

        # Rebaixamento sem censura de episódio já assistido: não volta pra "baixado".
        if _status_episodio(registro, numero_episodio_str) != "assistido":
            _definir_status_episodio(registro, numero_episodio_str, "baixado")
        registro.setdefault("episodios_baixado_em", {})[numero_episodio_str] = agora
        registro.get("downloads_em_andamento", {}).pop(numero_episodio_str, None)
        registro.get("episodios_falha_download", {}).pop(numero_episodio_str, None)  # travado sem alternativa que terminou
        concluidos += 1
        mudou = True
        print(f" [SISTEMA] 🎬 Download concluído: {registro['titulo']} Episódio {_rotulo_episodio(registro, numero_episodio_str)}.")
        if _hash_ainda_acompanhado(animes, info["hash"]):
            continue  # outro episódio do mesmo lote ainda depende do torrent
        try:
            cliente.torrents_delete(delete_files=False, torrent_hashes=info["hash"])
            registro.setdefault("episodios_removido_qbittorrent_em", {})[numero_episodio_str] = agora
        except Exception as e:
            print(f" [SISTEMA] Erro ao remover {registro['titulo']} Episódio {numero_episodio_str} da lista do qBittorrent: {e}")
        if info.get("lote"):
            _remover_subpastas_vazias_do_lote(torrents[0].save_path, arquivo_video)

    if mudou:
        _salvar_animes(animes)
    if travados:
        _tratar_downloads_travados(cliente, travados)
    return concluidos


SUFIXO_SEM_CENSURA = " [Sem Censura]"


def _hash_ainda_acompanhado(animes, hash_torrent):
    """True se algum episódio ainda acompanha `hash_torrent` (lote
    compartilhado) - aí o torrent não pode sair do qBittorrent."""
    return any(
        info.get("hash") == hash_torrent
        for registro in animes.values()
        for info in registro.get("downloads_em_andamento", {}).values()
    )


def _arquivo_do_episodio_no_lote(cliente, hash_torrent, numero_episodio, arquivos=None):
    """Arquivo de vídeo do lote cujo nome declara `numero_episodio`, ou None."""
    if arquivos is None:
        try:
            arquivos = cliente.torrents_files(torrent_hash=hash_torrent)
        except Exception:
            return None
    for f in arquivos:
        if f.name.lower().endswith(EXTENSOES_VIDEO) and _numero_episodio_no_nome_arquivo(f.name) == numero_episodio:
            return f
    return None


def _aplicar_selecao_lotes(cliente, animes):
    """Deixa baixando, em cada lote acompanhado, só os arquivos dos episódios
    pedidos (prioridade 0 no resto) e retoma o torrent, que foi adicionado
    com `stop_condition="MetadataReceived"`. Idempotente: só chama a API
    quando a seleção atual difere da desejada - cobre também um episódio
    novo pedido depois no mesmo lote. Sem metadado ainda, espera a próxima
    volta. Devolve True se registrou algo no estado."""
    por_hash = {}
    for chave, registro in animes.items():
        for ep, info in registro.get("downloads_em_andamento", {}).items():
            if info.get("lote"):
                por_hash.setdefault(info["hash"], []).append((registro, ep))
    mudou = False
    for hash_torrent, episodios in por_hash.items():
        try:
            arquivos = cliente.torrents_files(torrent_hash=hash_torrent)
        except Exception as e:
            print(f" [SISTEMA] Erro ao listar arquivos do lote {hash_torrent}: {e}")
            continue
        if not arquivos:
            continue  # metadado ainda não chegou
        desejados = set()
        for registro, ep in episodios:
            arquivo = _arquivo_do_episodio_no_lote(cliente, hash_torrent, int(ep), arquivos)
            if arquivo is not None:
                desejados.add(id(arquivo))
            elif not registro.get("episodios_erro_renomear", {}).get(ep, "").endswith("não encontrado no lote"):
                agora = datetime.now().strftime("%Y-%m-%d %H:%M")
                registro.setdefault("episodios_erro_renomear", {})[ep] = f"{agora}: arquivo do episódio não encontrado no lote"
                print(f" [SISTEMA] ⚠️ {registro['titulo']} Episódio {ep}: arquivo não encontrado no lote {hash_torrent}.")
                mudou = True
        if not desejados:
            continue  # nunca desligar tudo
        ligar = [getattr(f, "index", i) for i, f in enumerate(arquivos) if id(f) in desejados and f.priority == 0]
        desligar = [getattr(f, "index", i) for i, f in enumerate(arquivos) if id(f) not in desejados and f.priority != 0]
        infos = [registro["downloads_em_andamento"][ep] for registro, ep in episodios]
        # `lote_iniciado` cobre o lote em que todo arquivo foi pedido: nenhuma
        # prioridade muda, mas o torrent ainda precisa sair do stop_condition.
        if not ligar and not desligar and all(info.get("lote_iniciado") for info in infos):
            continue
        try:
            if desligar:
                cliente.torrents_file_priority(torrent_hash=hash_torrent, file_ids=desligar, priority=0)
            if ligar:
                cliente.torrents_file_priority(torrent_hash=hash_torrent, file_ids=ligar, priority=1)
            cliente.torrents_start(torrent_hashes=hash_torrent)
        except Exception as e:
            print(f" [SISTEMA] Erro ao selecionar arquivos do lote {hash_torrent}: {e}")
            continue
        for info in infos:
            info["lote_iniciado"] = True
        mudou = True
    return mudou


def _arquivo_pertence_ao_torrent(cliente, hash_torrent, caminho_arquivo):
    """True se o nome do arquivo está na lista de arquivos do PRÓPRIO torrent
    (qBittorrent `torrents_files`). False em qualquer erro - quem chama
    mantém a trava de divergência nesse caso."""
    try:
        arquivos = cliente.torrents_files(torrent_hash=hash_torrent)
    except Exception:
        return False
    nomes = {os.path.basename(f.name.replace("\\", "/")) for f in arquivos}
    return os.path.basename(caminho_arquivo) in nomes


def _tratar_downloads_travados(cliente, travados):
    """Tira do qBittorrent cada download sem progresso há
    `anime_download_travado_horas` e tenta o próximo magnet da página
    (baixar_episodio exclui os hashes em `episodios_magnets_tentados`). Sem
    alternativa, a falha fica em `episodios_falha_download` e vira alerta
    depois de `anime_alerta_falha_horas`. `delete_files=False`, mesmo padrão
    do resto do arquivo: nunca apaga arquivo no disco. Roda DEPOIS de
    verificar_downloads_em_andamento salvar o próprio estado, porque
    baixar_episodio carrega/salva o JSON por conta própria."""
    for chave, numero_episodio_str, hash_torrent, progresso in travados:
        animes = _carregar_animes()
        registro = animes.get(chave)
        if not registro or numero_episodio_str not in registro.get("downloads_em_andamento", {}):
            continue
        porcentagem = f"{progresso * 100:.0f}%" if progresso is not None else "fora do qBittorrent"
        rotulo_ep = _rotulo_episodio(registro, numero_episodio_str)
        ident = numero_episodio_str if _eh_especial(numero_episodio_str) else int(numero_episodio_str)
        if progresso is not None and not _opcoes_nao_tentadas(registro, ident, excluir=[hash_torrent])[1]:
            # 🔥 2026-09-25: sem outra opção na página, tirar o torrent só
            # piorava - some qualquer chance de um seeder aparecer. Fica no
            # qBittorrent, a contagem recomeça e a falha vira alerta.
            info = registro["downloads_em_andamento"][numero_episodio_str]
            info["progresso_em"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            if not info.get("sem_alternativa"):
                info["sem_alternativa"] = True
                print(f" [SISTEMA] ⚠️ {registro['titulo']} Episódio {rotulo_ep}: download travado ({porcentagem}) e sem outra opção na página - mantido no qBittorrent.")
            _salvar_animes(animes)
            _registrar_falha_download(chave, numero_episodio_str, f"download travado em {porcentagem}, sem outra opção na página")
            continue
        print(f" [SISTEMA] ⚠️ {registro['titulo']} Episódio {rotulo_ep}: download travado ({porcentagem}) - tentando outro magnet.")
        registro["downloads_em_andamento"].pop(numero_episodio_str, None)
        if not _hash_ainda_acompanhado(animes, hash_torrent):
            try:
                cliente.torrents_delete(delete_files=False, torrent_hashes=hash_torrent)
            except Exception as e:
                print(f" [SISTEMA] Erro ao remover torrent travado {hash_torrent}: {e}")
        tentados = registro.setdefault("episodios_magnets_tentados", {}).setdefault(numero_episodio_str, [])
        if hash_torrent not in tentados:
            tentados.append(hash_torrent)
        registro.setdefault("episodios_download_travado_em", {})[numero_episodio_str] = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}: {hash_torrent} parado em {porcentagem}"
        )
        _salvar_animes(animes)
        baixar_episodio(chave, registro, ident)


# ======================================================
# 📚 BIBLIOTECA LOCAL (baixado vs. assistido)
# ======================================================
# 🔥 Temporada ("S\d+") opcional no padrão (2026-08-03, pedido do usuário -
# incluir temporada no nome renomeado) - continua reconhecendo arquivos já
# renomeados ANTES dessa mudança (só "Título - E08.ext", sem temporada).
# Sufixo " [Sem Censura]" opcional (2026-09-24, SUFIXO_SEM_CENSURA).
_PADRAO_NOME_ARQUIVO = re.compile(r"^(.*) - (?:S\d+)?E(\d+)(?: \[Sem Censura\])?\.\w+$")


def _mapear_arquivos_por_titulo(pasta, ignorar=()):
    """Varre `pasta` (recursivo - o usuário pode organizar em subpastas) por
    arquivos de vídeo no padrão "{Título sanitizado} - S{SS}E{NN}.ext" (o
    mesmo que verificar_downloads_em_andamento produz - temporada opcional,
    reconhece também o padrão antigo sem temporada) e devolve
    {titulo_sanitizado: {numero_episodio, ...}}. Ignora qualquer arquivo fora
    do padrão (o usuário pode ter outras coisas nas mesmas pastas, não é
    problema nosso) e as subpastas em `ignorar` (caminhos completos)."""
    resultado = {}
    if not pasta or not os.path.isdir(pasta):
        return resultado
    ignorar = {os.path.normcase(os.path.normpath(p)) for p in ignorar}
    for raiz, subpastas, arquivos in os.walk(pasta):
        subpastas[:] = [s for s in subpastas if os.path.normcase(os.path.normpath(os.path.join(raiz, s))) not in ignorar]
        for nome in arquivos:
            if not nome.lower().endswith(EXTENSOES_VIDEO):
                continue
            match = _PADRAO_NOME_ARQUIVO.match(nome)
            if not match:
                continue
            titulo_sanitizado, numero_episodio = match.group(1), int(match.group(2))
            resultado.setdefault(titulo_sanitizado, set()).add(numero_episodio)
    return resultado


# ---- Anime finalizado em pasta própria (2026-09-24, pedido do usuário) ----
# Episódio de anime que já terminou de passar (anime_ja_finalizado) sai da
# raiz da pasta de downloads para "{pasta de assistidos}/{2026 3-Verão}/{Título}".
# A temporada é a do primeiro episódio organizado e fica gravada em
# `registro["temporada_organizada"]`, para os episódios seguintes irem para a
# mesma pasta mesmo que a estação mude no meio. Essas pastas ficam dentro da
# pasta de assistidos, mas o arquivo nelas conta como "baixado";
# "assistido" vem do player do MOIRAI (_concluir_episodio_assistido marca
# sem mover) ou do Painel.


def _pastas_de_temporada():
    """Caminhos completos das pastas "AAAA N-Estação" direto na pasta de assistidos."""
    pasta = obter_anime_pasta_assistidos()
    if not pasta or not os.path.isdir(pasta):
        return []
    return [
        os.path.join(pasta, nome) for nome in os.listdir(pasta)
        if _PADRAO_PASTA_TEMPORADA.match(nome) and os.path.isdir(os.path.join(pasta, nome))
    ]


def _dentro_de_pasta_temporada(caminho_arquivo):
    caminho = os.path.normcase(os.path.abspath(caminho_arquivo))
    return any(
        caminho.startswith(os.path.normcase(os.path.abspath(p)) + os.sep) for p in _pastas_de_temporada()
    )


def _titulo_do_nome_arquivo(nome):
    """Título sanitizado de um episódio renomeado pelo MOIRAI (normal ou especial), ou None."""
    match = _PADRAO_NOME_ARQUIVO.match(nome)
    if match:
        return match.group(1)
    match = _PADRAO_NOME_ESPECIAL.search(nome)
    return nome[:match.start()] if match else None


def anime_ja_iniciado(registro):
    """True se o usuário já começou a ver: algum episódio "assistido" ou
    progresso no MAL. 🔥 2026-09-25, pedido do usuário: anime que ele está
    assistindo continua na pasta de downloads mesmo finalizado ("so é para
    mandar animes q nem comecei a ver" - caso real Grand Blue 3ª Temporada,
    movido com 7 episódios já assistidos)."""
    return (any(status == "assistido" for status in registro.get("episodios", {}).values())
            or (registro.get("mal_ultimo_progresso_sincronizado") or 0) > 0)


def organizar_animes_finalizados():
    """Move os episódios de anime finalizado que o usuário ainda não começou
    a ver (anime_ja_iniciado) da raiz da pasta de downloads para a pasta
    própria do anime. Chamada pelo loop de downloads (main.py).
    Anime com lote em andamento ou pacote aguardando fica para depois: o
    arquivo de um lote continua no torrent até o último episódio, e mover
    antes faria o qBittorrent perder o arquivo. Episódio avulso baixando não
    segura os outros (🔥 2026-09-25: o especial sem seeder de Boku no Kokoro
    no Yabai Yatsu 2 prendia os 13 episódios prontos) - o arquivo dele ainda
    tem o nome do fansub e não casa com o título. Nunca sobrescreve: se o
    destino já existe, o arquivo fica onde está. Devolve quantos moveu."""
    pasta_downloads = obter_anime_pasta_downloads()
    pasta_assistidos = obter_anime_pasta_assistidos()
    if not pasta_downloads or not pasta_assistidos or not os.path.isdir(pasta_downloads):
        return 0
    animes = _carregar_animes()
    chave_por_titulo = {
        _sanitizar_nome_arquivo(r["titulo"]): c for c, r in animes.items()
        if r.get("interesse") == "tenho_interesse" and not anime_ja_iniciado(r)
        and not any(i.get("lote") for i in r.get("downloads_em_andamento", {}).values())
        and not r.get("pacote_completo", {}).get("aguardando_arquivos") and anime_ja_finalizado(r)
    }
    if not chave_por_titulo:
        return 0

    movidos, mudou = 0, False
    for nome in sorted(os.listdir(pasta_downloads)):
        origem = os.path.join(pasta_downloads, nome)
        if not nome.lower().endswith(EXTENSOES_VIDEO) or not os.path.isfile(origem):
            continue
        titulo_sanitizado = _titulo_do_nome_arquivo(nome)
        chave = chave_por_titulo.get(titulo_sanitizado)
        if not chave:
            continue
        registro = animes[chave]
        if "temporada_organizada" not in registro:
            registro["temporada_organizada"] = nome_pasta_temporada()
            mudou = True
        pasta_anime = os.path.join(pasta_assistidos, registro["temporada_organizada"], titulo_sanitizado)
        destino = os.path.join(pasta_anime, nome)
        if os.path.exists(destino):
            print(f" [SISTEMA] Anime finalizado: \"{nome}\" já existe em {pasta_anime}, ficou na pasta de downloads.")
            continue
        try:
            os.makedirs(pasta_anime, exist_ok=True)
            shutil.move(origem, destino)
        except OSError as e:
            print(f" [SISTEMA] Erro ao mover \"{nome}\" para a pasta do anime finalizado: {e}")
            continue
        movidos += 1
    if mudou:
        _salvar_animes(animes)
    if movidos:
        print(f" [SISTEMA] 📁 {movidos} episódio(s) de anime finalizado movido(s) para a pasta de cada anime.")
    return movidos


def obter_titulos_tenho_interesse():
    """[(titulo, chave), ...] pros animes "tenho_interesse" - usado pela
    categoria "🎬 Anime" do Menu Radial (2026-08-08) pra listar e depois achar
    o registro de volta a partir do título clicado."""
    animes = _carregar_animes()
    return [(r["titulo"], c) for c, r in animes.items() if r.get("interesse") == "tenho_interesse"]


def obter_titulos_para_assistir():
    """[(titulo, chave, capa_url), ...] só pros "tenho_interesse" que JÁ TÊM
    pelo menos 1 episódio baixado disponível agora (ver
    tem_episodio_disponivel_para_assistir) - usado pela categoria "🎬 Anime
    Tracker" do Menu Radial (IRIS, 2026-08-24, pedido do usuário: "seria os
    da categoria para assistir dentro do acompanhando"). Diferente de
    obter_titulos_tenho_interesse (usada noutros lugares) - listar ali quem
    ainda não tem nada baixado só criaria um clique morto (o "Assistir" do
    IRIS abre direto o 1º episódio baixado, sem seletor)."""
    animes = _carregar_animes()
    return [
        (r["titulo"], c, r.get("capa_url"))
        for c, r in animes.items()
        if r.get("interesse") == "tenho_interesse" and tem_episodio_disponivel_para_assistir(r)
    ]


def obter_primeiro_episodio_baixado(chave):
    """Acha o MENOR número de episódio já baixado (na pasta de downloads) pra
    um anime específico, e devolve (numero, caminho_completo) - ou (None,
    None) se não achar nenhum arquivo no padrão da Gaia pra esse anime. É o
    "próximo a assistir" na prática, não necessariamente o episódio 1
    (2026-08-08, pedido do usuário: "se tem do 3 ao 5, inicia o 3"). Usado
    pelo botão "▶️" do Assistente de Animes (ui/qt_modais/animes.py), que
    abre o resultado via `assistir_e_monitorar` (abaixo).

    🔥 Bug real reportado pelo usuário (2026-09-05): "quando clico no play,
    ele tá considerando episódio já assistido em vez de só da pasta
    downloads". `os.walk(pasta_downloads)` sozinho não sabe se um episódio já
    foi marcado "assistido" (ex.: uma cópia extra/residual do mesmo arquivo
    continuou na pasta de downloads depois da cópia "de verdade" já ter sido
    movida pra pasta de assistidos, ver `sincronizar_biblioteca_local`) -
    descarta aqui qualquer candidato cujo status registrado já seja
    "assistido", pra nunca reabrir episódio que o usuário já viu só porque o
    arquivo ainda aparece fisicamente na pasta errada."""
    registro = _carregar_animes().get(chave)
    if not registro:
        return None, None
    prefixo = _sanitizar_nome_arquivo(registro["titulo"])
    pasta = obter_anime_pasta_downloads()
    # Anime finalizado fica numa pasta de temporada dentro da de assistidos (organizar_animes_finalizados).
    pastas = [p for p in [pasta] + _pastas_de_temporada() if p and os.path.isdir(p)]
    episodios = registro.get("episodios", {})
    candidatos = {}
    for raiz, _, arquivos in (item for p in pastas for item in os.walk(p)):
        for nome in arquivos:
            if not nome.lower().endswith(EXTENSOES_VIDEO):
                continue
            match = _PADRAO_NOME_ARQUIVO.match(nome)
            if not match or match.group(1) != prefixo:
                continue
            numero = int(match.group(2))
            if episodios.get(str(numero)) == "assistido":
                continue
            candidatos[numero] = os.path.join(raiz, nome)
    if not candidatos:
        return None, None
    menor = min(candidatos)
    return menor, candidatos[menor]


def _resolver_comando_player(caminho_arquivo):
    """Acha o executável associado pelo USUÁRIO à extensão do arquivo (lê o
    registro do Windows - `UserChoice` da extensão, depois o comando "open"
    do ProgId associado) - resolvido pela extensão de verdade (não fixo num
    player), então continua certo se o usuário trocar de player ou se um
    episódio for .mp4 em vez de .mkv. None se não achar (ex.: nenhuma
    associação configurada, ou a chave do registro não existe)."""
    extensao = os.path.splitext(caminho_arquivo)[1].lower()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{extensao}\UserChoice") as chave:
            prog_id, _ = winreg.QueryValueEx(chave, "ProgId")
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as chave:
            comando, _ = winreg.QueryValueEx(chave, None)
    except OSError:
        return None
    comando = comando.strip()
    if comando.startswith('"'):
        return comando[1:comando.index('"', 1)]
    return comando.split()[0]


# 🔥 Aviso de episódio "reassistido" (2026-08-09, pedido do usuário: "quero ser
# notificado qnd for mover um episodio") - mesmo padrão de bridge que
# core/voice/tts.py::definir_callback_provedor_esgotado usa pra avisar o
# Discord sem esse módulo (camada de dados pura, sem acesso a discord_bot)
# precisar conhecer Discord/asyncio. Diferente do callback de tts.py (chamado
# de dentro de código já async, com `await` direto), `_concluir_episodio_assistido`
# roda numa `threading.Thread` comum (ver `assistir_e_monitorar` abaixo) - por
# isso o callback registrado por run.py precisa ser SÍNCRONO e fazer sua
# própria ponte (asyncio.run_coroutine_threadsafe) pro loop de verdade, não
# pode ser `async def` chamado direto daqui.
#
# 2026-08-09: primeira versão só avisava quando o episódio JÁ estava marcado
# "assistido" antes (detecção de reassistida) - o usuário corrigiu, o que ele
# queria de verdade é ser avisado sempre que a Gaia MESMA move um arquivo pra
# pasta de assistidos, sem essa distinção.
_callback_episodio_movido_assistidos = None


def definir_callback_episodio_movido_assistidos(callback):
    """`callback(titulo, numero_episodio)` - chamado (síncrono, thread do
    monitoramento) toda vez que `_concluir_episodio_assistido` move um arquivo
    pra pasta de assistidos."""
    global _callback_episodio_movido_assistidos
    _callback_episodio_movido_assistidos = callback


def _concluir_episodio_assistido(chave, numero_episodio, caminho_arquivo, ao_concluir=None):
    """Move o arquivo pra pasta de assistidos e deixa `sincronizar_biblioteca_local`
    (mesmo código que já roda a cada 5min, ver run.py) refletir o novo estado -
    não duplica a lógica de "o que conta como assistido", só move o arquivo pro
    lugar que ela já entende. Chama `sincronizar_progresso_mal()` (função
    definida mais abaixo neste arquivo - Python resolve pelo nome na hora de
    CHAMAR, não precisa estar definida antes no arquivo) na sequência, sem
    esperar o próximo ciclo do loop de 5min (2026-08-08, pedido do usuário:
    "e quando move a pasta ele já atualiza tudo na gaia e mal?" - antes o
    estado local ficava instantâneo mas o MAL só pegava no próximo ciclo).
    Episódio na pasta de um anime finalizado (2026-09-24) não sai de lá: o
    status vira "assistido" direto, porque a varredura conta essas pastas
    como "baixado"."""
    registro = _carregar_animes().get(chave, {})

    pasta_assistidos = obter_anime_pasta_assistidos()
    na_pasta_do_anime = _dentro_de_pasta_temporada(caminho_arquivo)
    if na_pasta_do_anime:
        with lock_estado_animes:
            animes = _carregar_animes()
            if chave in animes:
                animes[chave].setdefault("episodios", {})[str(numero_episodio)] = "assistido"
                animes[chave].setdefault("episodios_assistido_em", {}).setdefault(
                    str(numero_episodio), datetime.now().strftime("%Y-%m-%d"))
                _salvar_animes(animes)
    try:
        if pasta_assistidos and os.path.isfile(caminho_arquivo) and not na_pasta_do_anime:
            os.makedirs(pasta_assistidos, exist_ok=True)
            destino = os.path.join(pasta_assistidos, os.path.basename(caminho_arquivo))
            if not os.path.exists(destino):
                shutil.move(caminho_arquivo, destino)
    except OSError as e:
        print(f" [SISTEMA] Erro ao mover episódio pra pasta de assistidos: {e}")
    sincronizar_biblioteca_local()
    sincronizar_progresso_mal()
    onde = "ficou na pasta do anime" if na_pasta_do_anime else "movido pra pasta de assistidos sozinha"
    print(f" [SISTEMA] 🎬 Episódio {numero_episodio} marcado como assistido ({onde}).")
    if _callback_episodio_movido_assistidos:
        _callback_episodio_movido_assistidos(registro.get("titulo", chave), numero_episodio)
    if ao_concluir:
        ao_concluir()


def assistir_e_monitorar(chave, numero_episodio, caminho_arquivo, ao_concluir=None):
    """Abre o episódio no player padrão de verdade (via `_resolver_comando_player`)
    e monitora em background pra saber quando o usuário TERMINOU de assistir
    (2026-08-08, pedido do usuário: "não daria pra ver os aplicativos
    recentes? Se você sabe que abri o ep 1, depois sabe que abri outra
    coisa, você sabe que terminei"). Dois sinais, o que vier primeiro,
    sempre exigindo pelo menos `obter_limiar_minutos_assistido()` minutos
    (padrão 15) desde a abertura pra contar como "assistiu de verdade" (evita
    marcar só por ter aberto e fechado rápido sem querer):
      1. O processo do player fecha.
      2. O foco muda pra um processo DIFERENTE do player e fica assim por
         pelo menos 15s seguidos (cobre quem deixa o player aberto em
         segundo plano depois de terminar, sem fechar - só fechar não seria
         suficiente pra pegar esse caso).
    Devolve True se conseguiu abrir E vai monitorar; False se não achou o
    player (cai pro os.startfile normal, sem monitoramento - só abre)."""
    comando = _resolver_comando_player(caminho_arquivo)
    if not comando or not os.path.isfile(comando):
        os.startfile(caminho_arquivo)
        return False
    try:
        processo = subprocess.Popen([comando, caminho_arquivo])
    except OSError:
        os.startfile(caminho_arquivo)
        return False

    def _monitorar():
        import win32gui
        import win32process
        inicio = time.time()
        fora_do_foco_desde = None
        while True:
            time.sleep(5)
            limiar_segundos = obter_limiar_minutos_assistido() * 60
            tempo_aberto = time.time() - inicio

            if processo.poll() is not None:
                if tempo_aberto >= limiar_segundos:
                    _concluir_episodio_assistido(chave, numero_episodio, caminho_arquivo, ao_concluir)
                return

            try:
                hwnd_foco = win32gui.GetForegroundWindow()
                _, pid_foco = win32process.GetWindowThreadProcessId(hwnd_foco)
            except Exception:
                pid_foco = processo.pid

            if pid_foco != processo.pid:
                if fora_do_foco_desde is None:
                    fora_do_foco_desde = time.time()
                elif time.time() - fora_do_foco_desde >= 15 and tempo_aberto >= limiar_segundos:
                    _concluir_episodio_assistido(chave, numero_episodio, caminho_arquivo, ao_concluir)
                    return
            else:
                fora_do_foco_desde = None

    threading.Thread(target=_monitorar, daemon=True).start()
    return True


def sincronizar_biblioteca_local():
    """Pedido do usuário (2026-08-02): "manter controle dos episódios disponíveis,
    os que já baixei, e os que já assisti" - o usuário JÁ organiza isso manualmente
    (episódio baixado fica em obter_anime_pasta_downloads, e quando assiste, move
    pra obter_anime_pasta_assistidos) - esta função só REFLETE isso no estado de
    cada anime, varrendo as 2 pastas de verdade no disco, sem pedir nenhum passo
    novo do usuário. "Assistido" tem prioridade sobre "baixado" se o mesmo episódio
    aparecer (por engano) nas duas pastas. Chamada pelo mesmo loop de 5min que já
    checa downloads em andamento (ver _monitorar_downloads_animes_loop, run.py) e
    também ao abrir o modal de Animes (ui/qt_modais/animes.py) - é uma varredura
    de disco local, rápida, não pesa fazer com frequência. Também REVERTE
    "baixado" que sumiu de ambas as pastas (usuário apagou sem mover pra
    assistidos) - ver comentário mais abaixo."""
    animes = _carregar_animes()
    if not animes:
        return
    # 🔥 "sem_interesse" fica de fora (2026-08-14, pedido do usuário: registro
    # enxuto, sem episódios/downloads rastreados) - sem essa exclusão, um
    # arquivo de vídeo que coincidisse com o título sanitizado de um anime
    # "sem_interesse" botaria "episodios"/"episodios_assistido_em" de volta
    # nele, mesmo sem nenhum download disparado pela Gaia pra esse anime.
    titulo_sanitizado_para_chave = {
        _sanitizar_nome_arquivo(r["titulo"]): c for c, r in animes.items() if r.get("interesse") != "sem_interesse"
    }

    baixados = _mapear_arquivos_por_titulo(obter_anime_pasta_downloads())
    # Pasta de anime finalizado (organizar_animes_finalizados) fica dentro da
    # de assistidos, mas o que está nela conta como baixado.
    pastas_temporada = _pastas_de_temporada()
    for pasta_temporada in pastas_temporada:
        for titulo_sanitizado, numeros in _mapear_arquivos_por_titulo(pasta_temporada).items():
            baixados.setdefault(titulo_sanitizado, set()).update(numeros)
    assistidos = _mapear_arquivos_por_titulo(obter_anime_pasta_assistidos(), ignorar=pastas_temporada)

    mudou = False
    for titulo_sanitizado, chave in titulo_sanitizado_para_chave.items():
        episodios = animes[chave].setdefault("episodios", {})
        # 🔥 data da 1ª vez que cada episódio virou "assistido" (2026-08-02,
        # feature 3 do docs/TODO.md - "há 2 semanas você não assiste X") - só
        # a TRANSIÇÃO importa (não sobrescreve se já estava assistido antes).
        datas_assistido = animes[chave].setdefault("episodios_assistido_em", {})
        for numero_episodio in assistidos.get(titulo_sanitizado, ()):
            chave_ep = str(numero_episodio)
            if episodios.get(chave_ep) != "assistido":
                episodios[chave_ep] = "assistido"
                datas_assistido[chave_ep] = datetime.now().strftime("%Y-%m-%d")
                mudou = True
        datas_baixado = animes[chave].setdefault("episodios_baixado_em", {})
        for numero_episodio in baixados.get(titulo_sanitizado, ()):
            chave_ep = str(numero_episodio)
            if episodios.get(chave_ep) != "assistido" and episodios.get(chave_ep) != "baixado":
                episodios[chave_ep] = "baixado"
                datas_baixado.setdefault(chave_ep, datetime.now().strftime("%Y-%m-%d %H:%M"))
                mudou = True

        # 🔥 Bug real reportado (2026-08-16): "Para assistir" continuava
        # listando anime cujo episódio o usuário já tinha APAGADO de
        # `obter_anime_pasta_downloads` sem mover pra "assistidos" (decidiu
        # pular, assistiu por streaming, limpou espaço em disco etc.) - até
        # aqui, esta função só PROMOVIA status (nunca-baixado→baixado→
        # assistido), nunca revertia. "baixado" que sumiu de AMBAS as pastas
        # volta a "nunca baixado" (remove a chave), pra
        # tem_episodio_disponivel_para_assistir() parar de contar ele.
        # Registro de auditoria (mesmo pedido de 2026-08-16 que criou os
        # dicts "_em" acima) - NÃO apaga `episodios_baixado_em`/
        # `episodios_renomeado_em` (fica registrado que já existiu, útil pra
        # investigar depois), só anota a reversão num dict próprio.
        numeros_no_disco = baixados.get(titulo_sanitizado, set()) | assistidos.get(titulo_sanitizado, set())
        datas_revertido = animes[chave].setdefault("episodios_revertido_em", {})
        for chave_ep in [c for c, status in episodios.items() if status == "baixado" and int(c) not in numeros_no_disco]:
            del episodios[chave_ep]
            datas_revertido[chave_ep] = (
                f"{datetime.now().strftime('%Y-%m-%d %H:%M')}: arquivo não encontrado em nenhuma "
                f"das 2 pastas (apagado sem mover pra assistidos)"
            )
            mudou = True

    if mudou:
        _salvar_animes(animes)


_PADRAO_EPISODIO_SXXEXX = re.compile(r"S(\d{1,2})E(\d{1,4})", re.IGNORECASE)
_PADRAO_EPISODIO_TRACO = re.compile(r"-\s*(\d{1,4})\s*(?:\[|\(|_|\.|$)")
# 🔥 Limiares configuráveis no Painel (obter_renomear_confianca_minima/
# obter_renomear_confianca_parcial/obter_renomear_margem_parcial,
# brain_store.py - percentual inteiro, calibrado com dados reais, ver
# docs/CORRECOES.md 2026-08-08) - 2026-08-08, pedido do usuário ("onde q
# configuro essa segunda opção?"). Lidos frescos a cada chamada (não fixados
# no import), mesmo padrão dos outros limiares desse arquivo.


def _similaridade_parcial(a, b):
    """Similaridade "parcial" - um texto pode ser um apelido/abreviação curta
    de fansub que aparece só como uma PARTE do outro (ex.: "jukishi" pra
    "Tsuihou sareta Tensei Juukishi..." - comparar os 2 textos INTEIROS via
    _similaridade_titulo dilui a pontuação quando um é bem mais curto que o
    outro, real: 0.21 pra esse par, mesmo sendo claramente o mesmo anime).
    Desliza o texto mais curto por cima do mais longo procurando o melhor
    recorte - mesma técnica de "partial ratio" de bibliotecas de fuzzy
    matching. Ainda assim arriscado sozinho (ver docs/CORRECOES.md 2026-08-08
    - "jukishi" bate igual em 2 animes diferentes que só compartilham a
    palavra "kishi") - por isso NUNCA usado sem exigir margem sobre o 2º
    colocado, ver _casar_arquivo_com_interesse."""
    texto_a, texto_b = _normalizar_titulo_comparacao(a), _normalizar_titulo_comparacao(b)
    curto, longo = (texto_a, texto_b) if len(texto_a) <= len(texto_b) else (texto_b, texto_a)
    if not curto:
        return 0.0
    melhor = 0.0
    for bloco in SequenceMatcher(None, curto, longo).get_matching_blocks():
        inicio = max(0, bloco.b - (len(curto) - bloco.size))
        recorte = longo[inicio:inicio + len(curto)]
        melhor = max(melhor, SequenceMatcher(None, curto, recorte).ratio())
    return melhor


def _casar_arquivo_com_interesse(texto_comparacao, animes_interesse):
    """Acha qual anime "tenho_interesse" bate com `texto_comparacao` (nome de
    arquivo já limpo, ver _limpar_nome_arquivo_para_comparacao) - 2 tentativas
    em sequência:
    1. Comparação do texto INTEIRO (`_similaridade_titulo`) - funciona bem
       quando o release usa o nome completo do anime. Confiança alta
       (`obter_renomear_confianca_minima`, Painel) já basta, sem precisar de
       margem - testado real: o par mais parecido que NÃO deveria casar não
       passou de 0.51, bem longe do limiar.
    2. Se nada bateu, tenta PARCIAL (`_similaridade_parcial`) - pega apelido/
       abreviação curta de fansub. Essa SEMPRE exige margem clara sobre o 2º
       colocado (`obter_renomear_margem_parcial`) além da confiança mínima
       (`obter_renomear_confianca_parcial`) - um texto curto pode empatar por
       acidente entre 2 animes que só compartilham uma palavra (caso real:
       "jukishi" bateu igual em "Tsuihou sareta Tensei Juukishi..." E
       "Gaikotsu Kishi-sama..." - "kishi" aparece nos dois - sem margem, não
       arrisca escolher errado).
    Devolve (registro, confiança) do escolhido, ou (None, 0) se nada bater
    com segurança."""
    pontuados_inteiro = sorted(
        ((r, _similaridade_titulo(texto_comparacao, r["titulo"])) for _, r in animes_interesse),
        key=lambda par: par[1], reverse=True,
    )
    if pontuados_inteiro[0][1] >= obter_renomear_confianca_minima() / 100:
        return pontuados_inteiro[0]

    pontuados_parcial = sorted(
        ((r, _similaridade_parcial(texto_comparacao, r["titulo"])) for _, r in animes_interesse),
        key=lambda par: par[1], reverse=True,
    )
    melhor, melhor_pont = pontuados_parcial[0]
    segundo_pont = pontuados_parcial[1][1] if len(pontuados_parcial) > 1 else 0.0
    if melhor_pont >= obter_renomear_confianca_parcial() / 100 and (melhor_pont - segundo_pont) >= obter_renomear_margem_parcial() / 100:
        return melhor, melhor_pont
    return None, 0.0


def _extrair_episodio_de_nome_arquivo(nome):
    """Tenta reconhecer o número do episódio (e a temporada, se explícita) a
    partir de um nome de arquivo QUALQUER baixado por fora do fluxo automático
    - cada grupo de fansub numera diferente. Reconhece "S01E05" (com ou sem
    pontuação ao redor) e "- NN" (traço seguido de número, o padrão mais comum
    de release single-episódio, ex.: "... - 05 [1080p...]"). Devolve
    (temporada ou None, episódio) ou (None, None) se não achar nada
    reconhecível - nesse caso o chamador não arrisca renomear."""
    m = _PADRAO_EPISODIO_SXXEXX.search(nome)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _PADRAO_EPISODIO_TRACO.search(nome)
    if m:
        return None, int(m.group(1))
    return None, None


def _limpar_nome_arquivo_para_comparacao(nome):
    """Tira tudo que não é o título em si de um nome de arquivo baixado por
    fora - tags de grupo/qualidade entre colchetes/parênteses (ex.: "[Judas]",
    "[1080p HEVC]", "(MultiSub)"), extensão, pontos/underscores usados como
    separador de palavra, e o marcador de episódio do fim (já extraído à parte
    por _extrair_episodio_de_nome_arquivo)."""
    sem_extensao = os.path.splitext(nome)[0]
    sem_colchetes = re.sub(r"\[[^\]]*\]", " ", sem_extensao)
    sem_parenteses = re.sub(r"\([^)]*\)", " ", sem_colchetes)
    sem_pontuacao = sem_parenteses.replace(".", " ").replace("_", " ")
    sem_episodio_sxxexx = re.sub(r"S\d{1,2}E\d{1,4}.*$", " ", sem_pontuacao, flags=re.IGNORECASE)
    sem_episodio_traco = re.sub(r"-\s*\d{1,4}\s*$", " ", sem_episodio_sxxexx)
    return re.sub(r"\s+", " ", sem_episodio_traco).strip()


def renomear_biblioteca_existente(dry_run=False):
    """Varre as 2 pastas (baixados + assistidos) por vídeo que ainda NÃO está
    no padrão de nome da Gaia (`_PADRAO_NOME_ARQUIVO`) - baixado manualmente
    por fora, ou baixado antes dessa renomeação existir - e tenta casar por
    título com um anime "tenho_interesse" (2026-08-08, pedido do usuário:
    "renomear os vídeos existentes de acordo com os animes que marquei ter
    interesse"). Se achar um candidato de confiança alta
    (`obter_renomear_confianca_minima`, Painel), renomeia pro padrão
    "{Título} - S{SS}E{NN}.ext" - mesma função de comparação
    (`_similaridade_titulo`) já usada e testada pra casar com o MAL.

    MUITO conservador de propósito - as pastas do usuário têm filme, série,
    vídeo pessoal e até spam misturado com anime de verdade (confirmado
    varrendo a pasta real), então só renomeia com confiança alta, nunca um
    "melhor palpite" - testado real: matches genuínos saem 0.91-1.00 de
    confiança, o par mais parecido que NÃO deveria casar (títulos de anime
    diferentes que compartilham uma palavra) não passou de 0.51.

    `dry_run=True` só REPORTA o que faria, sem tocar em nenhum arquivo -
    sempre rodar assim primeiro antes de confiar. Nunca sobrescreve um arquivo
    já existente com o nome novo. Devolve ([(caminho_antigo, caminho_novo,
    confiança), ...], [(caminho, titulo_anime, numero_episodio,
    ultimo_conhecido), ...]) - a 2ª lista é quem bateu por título mas ficou
    de fora por causa da trava de numeração acumulada (ver comentário
    abaixo) - achado real, 2026-08-29: "cliquei no botão pra renomear, mas
    tao reconhecendo" - o pulo por essa trava era 100% silencioso, sem
    nenhum jeito de saber que era ISSO (episódio recém-baixado, registro
    ainda sem o "último episódio visto" atualizado - se resolve sozinho no
    próximo ciclo do MOIRAI, mas até lá o usuário só via "nada aconteceu")."""
    animes_completo = _carregar_animes()
    animes_interesse = [
        (chave, registro) for chave, registro in animes_completo.items()
        if registro.get("interesse") == "tenho_interesse"
    ]
    if not animes_interesse:
        return [], []
    # 🔥 Bug real encontrado 2026-08-08 ("por que Youjo Senki não foi
    # renomeado?"): _PADRAO_NOME_ARQUIVO só confere a FORMA do nome ("{algo} -
    # SxxExx.ext"), não se "{algo}" é de fato o título sanitizado de um anime
    # rastreado - "[Judas] Youjo Senki - S02E03.mkv" batia na forma (o Judas
    # também usa "- SxxExx") e era pulado como "já correto", mesmo o prefixo
    # sendo "[Judas] Youjo Senki", não "Youjo Senki II" de verdade. Só pula
    # de verdade se o prefixo capturado for EXATAMENTE o título sanitizado de
    # algum "tenho_interesse".
    titulos_sanitizados_interesse = {_sanitizar_nome_arquivo(r["titulo"]) for _, r in animes_interesse}

    resultados = []
    pendentes_numeracao = []
    caminhos_vistos = set()  # 🔥 pastas/assistidos podem ser aninhadas (ex.: "assistidos" DENTRO de "baixados") - sem isso, o mesmo arquivo seria visitado 2x, um pra cada pasta
    for pasta in (obter_anime_pasta_downloads(), obter_anime_pasta_assistidos()):
        if not pasta or not os.path.isdir(pasta):
            continue
        for raiz, _, arquivos in os.walk(pasta):
            for nome in arquivos:
                caminho_normalizado = os.path.normcase(os.path.abspath(os.path.join(raiz, nome)))
                if caminho_normalizado in caminhos_vistos:
                    continue
                caminhos_vistos.add(caminho_normalizado)
                if not nome.lower().endswith(EXTENSOES_VIDEO):
                    continue
                match_padrao_gaia = _PADRAO_NOME_ARQUIVO.match(nome)
                if match_padrao_gaia and match_padrao_gaia.group(1) in titulos_sanitizados_interesse:
                    continue  # 🔥 já no padrão da Gaia, com o título certo - não precisa mexer
                if _PADRAO_NOME_ESPECIAL.search(nome):
                    continue  # especial já renomeado ("E06.5 - Especial 1") - não é o E06

                temporada_arquivo, numero_episodio = _extrair_episodio_de_nome_arquivo(nome)
                if numero_episodio is None:
                    continue  # 🔥 sem número de episódio reconhecível - não arrisca

                texto_comparacao = _limpar_nome_arquivo_para_comparacao(nome)
                registro, confianca = _casar_arquivo_com_interesse(texto_comparacao, animes_interesse)
                if registro is None:
                    continue

                # 🔥 Bug real encontrado 2026-08-08 - alguns grupos de fansub
                # numeram episódio de forma ACUMULADA pra franquia toda (ex.:
                # "[Judas] Dr Stone - Science Future - S04E25.mkv" pra o que
                # o DarkMahou trata como "Part 3 Episódio 1" - Judas conta a
                # partir da 1ª temporada da franquia, não reinicia por
                # "parte"/temporada) - usar esse número direto criaria
                # "Episódio 25" numa entrada que só tem 13 episódios de
                # verdade. Se o número extraído do arquivo for MAIOR que o
                # último episódio já visto de verdade pra esse anime (scraping
                # do DarkMahou, `ultimo_episodio_visto`), a numeração do
                # arquivo não bate com a da entrada rastreada - não arrisca,
                # pula (fica pro usuário renomear manualmente, sabendo o
                # deslocamento certo entre as 2 numerações).
                ultimo_conhecido = registro.get("ultimo_episodio_visto")
                if ultimo_conhecido is not None and numero_episodio > ultimo_conhecido:
                    pendentes_numeracao.append((os.path.join(raiz, nome), registro["titulo"], numero_episodio, ultimo_conhecido))
                    continue

                numero_temporada = temporada_arquivo or _detectar_numero_temporada(registro["titulo"])
                extensao = os.path.splitext(nome)[1]
                nome_novo = f"{_sanitizar_nome_arquivo(registro['titulo'])} - S{numero_temporada:02d}E{numero_episodio:02d}{extensao}"
                caminho_antigo = os.path.join(raiz, nome)
                caminho_novo = os.path.join(raiz, nome_novo)
                if os.path.exists(caminho_novo) or caminho_antigo == caminho_novo:
                    continue  # 🔥 já existe um arquivo com esse nome - nunca sobrescreve

                resultados.append((caminho_antigo, caminho_novo, confianca))
                if not dry_run:
                    try:
                        os.rename(caminho_antigo, caminho_novo)
                        # 🔥 Registro de auditoria (2026-08-16, pedido do
                        # usuário) - mesmo dict que verificar_downloads_em_
                        # andamento usa, pra ficar tudo no mesmo lugar não
                        # importa qual caminho renomeou o arquivo.
                        registro.setdefault("episodios_renomeado_em", {})[str(numero_episodio)] = (
                            f"{datetime.now().strftime('%Y-%m-%d %H:%M')} (renomear_biblioteca_existente, confiança {confianca:.2f})"
                        )
                    except OSError as e:
                        print(f" [SISTEMA] Erro ao renomear {caminho_antigo}: {e}")
    if not dry_run and resultados:
        _salvar_animes(animes_completo)
    return resultados, pendentes_numeracao


def _extrair_hashes_por_episodio(url_anime):
    """Baixa a página do anime e devolve {numero_episodio: {hash1, hash2, ...}}
    - TODOS os hashes de magnet de TODAS as opções (toda linha de fonte/
    qualidade/idioma do bloco, não só a legendada que _extrair_opcoes_download
    usa pro download automático) de cada episódio. Usado por
    renomear_por_hash_qbittorrent pra casar um torrent JÁ no qBittorrent (
    baixado pela Gaia OU manualmente, não importa) com o episódio certo -
    2026-08-08, pedido do usuário: "olhar meio que pela fonte, pra saber que
    são do link 1/2/3, referentes ao anime ABC" - o HASH de um torrent nunca
    muda, não importa como o usuário renomeou o arquivo depois, então é bem
    mais confiável que comparar texto (sem a ambiguidade de apelido de
    fansub nem o risco de numeração acumulada, ver docs/CORRECOES.md
    2026-08-08). Dict vazio em qualquer falha - nunca lança exceção."""
    try:
        html = _obter_html_darkmahou(url_anime)
    except Exception as e:
        print(f" [SISTEMA] Erro ao acessar página do anime ({url_anime}): {e}")
        return {}

    soup = BeautifulSoup(html, "html.parser")
    resultado = {}
    for bloco in soup.find_all("div", class_="soraddl"):
        titulo_bloco = bloco.find("h3")
        if not titulo_bloco:
            continue
        numero_episodio = _numero_do_bloco(titulo_bloco.get_text(strip=True))
        if numero_episodio is None:
            continue
        hashes = set()
        for a in bloco.find_all("a", href=re.compile(r"^magnet:")):
            m = re.search(r"btih:([a-fA-F0-9]{40})", a["href"])
            if m:
                hashes.add(m.group(1).lower())
        if hashes:
            resultado[numero_episodio] = hashes
    return resultado


def _remover_subpastas_vazias_do_lote(pasta_base, arquivo_original):
    """Tira as subpastas do lote que ficaram vazias depois de os episódios
    irem pra raiz. `os.rmdir` só remove pasta vazia: qualquer arquivo que
    sobrar (episódio não pedido, legenda) mantém a pasta, nunca é apagado."""
    pasta = os.path.dirname(arquivo_original)
    base = os.path.normcase(os.path.abspath(pasta_base))
    while os.path.normcase(os.path.abspath(pasta)).startswith(base + os.sep):
        try:
            os.rmdir(pasta)
        except OSError:
            return
        pasta = os.path.dirname(pasta)


def _renomear_arquivo_via_api_qbittorrent(cliente, torrent, arquivo_video, nome_novo, na_raiz=False):
    """Renomeia o arquivo de conteúdo de um torrent chamando
    `torrents_rename_file` da própria API do qBittorrent, em vez de
    `os.rename` cru (2026-08-08, pedido do usuário) - assim o qBittorrent
    continua sabendo onde o arquivo está e pode seguir semeando normalmente
    depois do rename, sem virar "missingFiles" (bug real visto na biblioteca
    do usuário - vários torrents já "missingFiles" provavelmente por causa
    de rename manual feito assim antes). Precisa do caminho RELATIVO do
    arquivo dentro do torrent (não o caminho absoluto no disco) - calculado a
    partir de `torrent.save_path`, funciona tanto pra torrent de arquivo
    único quanto pra torrent com pasta (multi-arquivo).

    `na_raiz=True` (lote, 2026-09-24): o caminho novo não repete a subpasta
    do torrent, então o qBittorrent move o arquivo pra `save_path`."""
    caminho_relativo_atual = os.path.relpath(arquivo_video, torrent.save_path)
    pasta_relativa = "" if na_raiz else os.path.dirname(caminho_relativo_atual)
    caminho_relativo_novo = os.path.join(pasta_relativa, nome_novo) if pasta_relativa else nome_novo
    cliente.torrents_rename_file(torrent_hash=torrent.hash, old_path=caminho_relativo_atual, new_path=caminho_relativo_novo)


def renomear_por_hash_qbittorrent(dry_run=False, remover_da_lista_depois=True):
    """Casa torrent JÁ no qBittorrent (baixado pela Gaia OU manualmente, não
    importa) com o episódio certo comparando o HASH real do torrent contra
    TODOS os hashes de magnet publicados na página de cada anime
    "tenho_interesse" - MUITO mais confiável que comparar nome de arquivo
    (renomear_biblioteca_existente): hash é único e não muda não importa como
    o usuário renomeou o arquivo depois, elimina toda ambiguidade de apelido/
    abreviação de fansub e o risco de numeração acumulada (ver
    docs/CORRECOES.md 2026-08-08). Só funciona pra torrent que AINDA está no
    qBittorrent (se o usuário já removeu da lista depois de terminar, não tem
    hash pra comparar - nesse caso só renomear_biblioteca_existente, por
    nome, ainda serve).

    Renomeia via API do qBittorrent (`_renomear_arquivo_via_api_qbittorrent`),
    não `os.rename` cru - mantém o torrent semeando normalmente do novo nome.
    Só mexe em torrent 100% completo (`progress >= 1.0` - nunca em algo ainda
    baixando, por segurança). `remover_da_lista_depois=True` (padrão, pedido
    do usuário) tira a entrada da LISTA do qBittorrent depois de renomear com
    sucesso (`delete_files=False` sempre - nunca apaga o arquivo de verdade,
    só para de acompanhar/semear aquele torrent específico - deixe False se
    quiser continuar semeando).

    `dry_run=True` só REPORTA sem tocar em nada (nem renomear, nem remover).
    Nunca sobrescreve um arquivo já existente. Devolve [(caminho_antigo,
    caminho_novo, "hash"), ...] dos que renomeou (ou renomearia)."""
    animes_interesse = [
        (chave, registro) for chave, registro in _carregar_animes().items()
        if registro.get("interesse") == "tenho_interesse"
    ]
    if not animes_interesse or not qbittorrent_configurado():
        return []

    mapa_hashes = {}
    for _, registro in animes_interesse:
        for numero_episodio, hashes in _extrair_hashes_por_episodio(registro["url"]).items():
            for h in hashes:
                mapa_hashes[h] = (registro, numero_episodio)
    if not mapa_hashes:
        return []

    try:
        cliente = _cliente_qbittorrent()
    except Exception as e:
        print(f" [SISTEMA] Erro ao conectar no qBittorrent pra renomear por hash: {e}")
        return []

    resultados = []
    for torrent in cliente.torrents_info():
        casado = mapa_hashes.get(torrent.hash.lower())
        if not casado:
            continue
        if torrent.progress < 1.0:
            continue  # 🔥 nunca mexe em torrent ainda baixando
        registro, numero_episodio = casado
        caminho_conteudo = torrent.content_path
        if not caminho_conteudo or not os.path.exists(caminho_conteudo):
            continue
        arquivo_video = _maior_arquivo_video(caminho_conteudo)
        if not arquivo_video:
            continue

        numero_temporada = _detectar_numero_temporada(registro["titulo"])
        extensao = os.path.splitext(arquivo_video)[1]
        nome_novo = f"{_sanitizar_nome_arquivo(registro['titulo'])} - S{numero_temporada:02d}E{numero_episodio:02d}{extensao}"
        caminho_novo = os.path.join(os.path.dirname(arquivo_video), nome_novo)
        if os.path.exists(caminho_novo) or arquivo_video == caminho_novo:
            continue  # 🔥 já existe (ou já está certo) - nunca sobrescreve

        resultados.append((arquivo_video, caminho_novo, "hash"))
        if not dry_run:
            try:
                _renomear_arquivo_via_api_qbittorrent(cliente, torrent, arquivo_video, nome_novo)
            except Exception as e:
                print(f" [SISTEMA] Erro ao renomear {arquivo_video} via API do qBittorrent: {e}")
                continue
            if remover_da_lista_depois:
                try:
                    cliente.torrents_delete(delete_files=False, torrent_hashes=torrent.hash)
                except Exception as e:
                    print(f" [SISTEMA] Erro ao remover {torrent.name} da lista do qBittorrent: {e}")
    return resultados


def renomear_biblioteca_completa(dry_run=False):
    """Renomeação em 2 camadas, mais confiável primeiro (2026-08-08, pedido
    do usuário) - 1ª por HASH do torrent (renomear_por_hash_qbittorrent, 100%
    confiável, cobre qualquer torrent AINDA no qBittorrent, mesmo baixado por
    fora); 2ª por título do nome de arquivo (renomear_biblioteca_existente,
    cobre o resto - torrent já removido do qBittorrent depois de terminar,
    só resta o arquivo, sem hash pra comparar). Depois das 2, converte pra
    .mkv todo .mp4 que ficou no padrão da Gaia (converter_mp4_para_mkv,
    biblioteca com container único/consistente). Seguro rodar tudo em
    sequência - o que uma etapa já resolveu sai do padrão "ainda pendente" e
    a próxima simplesmente não mexe de novo. Devolve (resultados, pendentes)
    - resultados combina as 3 etapas (as 2 primeiras marcam o método -
    "hash"/confiança -, a conversão marca "mp4->mkv"); pendentes vem só da
    etapa por nome (ver renomear_biblioteca_existente) - vídeo que bateu com
    um "tenho interesse" mas ficou de fora por episódio mais novo que o
    último conhecido (provavelmente só falta o MOIRAI atualizar o
    registro, ver docs/CORRECOES.md 2026-08-29)."""
    resultados_hash = renomear_por_hash_qbittorrent(dry_run=dry_run)
    resultados_nome, pendentes = renomear_biblioteca_existente(dry_run=dry_run)
    resultados_mkv = [(mp4, mkv, "mp4->mkv") for mp4, mkv in converter_mp4_para_mkv(dry_run=dry_run)]
    return resultados_hash + resultados_nome + resultados_mkv, pendentes


def _remuxar_para_mkv(caminho_mp4):
    """Remuxa (SEM recodificar - streams copiados direto via `-c copy`, rápido
    e sem nenhuma perda de qualidade, só troca o container) um vídeo .mp4 pra
    .mkv, via `ffmpeg` (precisa estar instalado e no PATH) - 2026-08-08,
    pedido do usuário: "troque os mp4 pra mkv" (biblioteca com container
    único/consistente - .mkv já é o padrão que a Gaia produz ao baixar via
    magnet). Apaga o .mp4 original só depois do .mkv novo existir de verdade
    (nunca perde o arquivo se a conversão falhar no meio). Devolve o caminho
    do .mkv novo, ou None se o ffmpeg não estiver disponível, já existir um
    arquivo com esse nome, ou a conversão falhar (nesse caso o .mp4 original
    continua intocado)."""
    caminho_mkv = os.path.splitext(caminho_mp4)[0] + ".mkv"
    if os.path.exists(caminho_mkv):
        return None
    try:
        resultado = subprocess.run(
            ["ffmpeg", "-y", "-i", caminho_mp4, "-c", "copy", caminho_mkv],
            capture_output=True, timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f" [SISTEMA] Não consegui converter {caminho_mp4} pra mkv (ffmpeg indisponível ou demorou demais): {e}")
        return None
    if resultado.returncode != 0 or not os.path.exists(caminho_mkv):
        print(f" [SISTEMA] ffmpeg falhou ao converter {caminho_mp4}: {resultado.stderr.decode(errors='ignore')[-500:]}")
        return None
    try:
        os.remove(caminho_mp4)
    except OSError as e:
        print(f" [SISTEMA] Convertido pra mkv, mas não consegui apagar o .mp4 original ({caminho_mp4}): {e}")
    return caminho_mkv


def converter_mp4_para_mkv(dry_run=False):
    """Remuxa todo vídeo .mp4 já no padrão de nome da Gaia
    (`_PADRAO_NOME_ARQUIVO` - ou seja, já renomeado por
    renomear_biblioteca_completa/verificar_downloads_em_andamento) pra .mkv -
    2026-08-08, pedido do usuário. Só mexe no que já está no padrão da Gaia,
    de propósito - as pastas do usuário têm filme/série/vídeo pessoal em
    .mp4 misturado com anime (confirmado varrendo a pasta real), e essa
    função NUNCA deve tocar nisso. `dry_run=True` só REPORTA sem converter
    nada. Devolve [(caminho_mp4, caminho_mkv), ...] dos convertidos (ou que
    seriam)."""
    resultados = []
    caminhos_vistos = set()
    for pasta in (obter_anime_pasta_downloads(), obter_anime_pasta_assistidos()):
        if not pasta or not os.path.isdir(pasta):
            continue
        for raiz, _, arquivos in os.walk(pasta):
            for nome in arquivos:
                if not nome.lower().endswith(".mp4"):
                    continue
                if not _PADRAO_NOME_ARQUIVO.match(nome):
                    continue  # 🔥 só mexe no que já está no padrão da Gaia
                caminho_mp4 = os.path.join(raiz, nome)
                caminho_normalizado = os.path.normcase(os.path.abspath(caminho_mp4))
                if caminho_normalizado in caminhos_vistos:
                    continue
                caminhos_vistos.add(caminho_normalizado)
                caminho_mkv = os.path.splitext(caminho_mp4)[0] + ".mkv"
                if os.path.exists(caminho_mkv):
                    continue
                resultados.append((caminho_mp4, caminho_mkv))
                if not dry_run:
                    _remuxar_para_mkv(caminho_mp4)
    return resultados


# ======================================================
# 🔗 CASAMENTO COM MYANIMELIST (Fase 2 - docs/TODO.md)
# ======================================================
# 🔥 Limiares configuráveis no Painel (obter_mal_confianca_minima/
# obter_mal_margem_minima, brain_store.py - percentual inteiro, ver
# casar_animes_com_mal) - 2026-08-03, pedido do usuário ("tudo que foi feito
# fixo, é bom deixar ser configurável"). Lidos frescos a cada checagem (não
# como default de parâmetro) pra uma mudança no Painel valer já na próxima
# vez, sem precisar reiniciar a Galateia.


def _normalizar_titulo_comparacao(titulo):
    """Baixa pra minúsculo e tira pontuação (":", "-", etc.) antes de comparar -
    o DarkMahou e o MAL frequentemente escrevem o mesmo título com pontuação
    diferente (ex.: "Zhan Shen: Fanchen Shenyu" vs "Zhan Shen - Fanchen Shenyu")."""
    return re.sub(r"[^\w\s]", "", titulo.lower()).strip()


def _similaridade_titulo(a, b):
    return SequenceMatcher(None, _normalizar_titulo_comparacao(a), _normalizar_titulo_comparacao(b)).ratio()


_SUFIXOS_ORDINAL_EN = {1: "1st", 2: "2nd", 3: "3rd"}  # 4+ usa "th" (4th, 5th, 6th...)


def _titulo_para_busca_mal(titulo):
    """Traduz o sufixo de temporada em português - "Nª Temporada"/"N Temporada",
    padrão comum no DarkMahou pra 2ª temporada em diante - pro formato em
    inglês que o MAL usa de verdade ("Nth Season"). Bug real encontrado
    2026-08-03: buscar "Tensei shitara Slime Datta Ken 4ª Temporada" no MAL
    não trazia a 4ª temporada de verdade (id 59970) nem no top 10 - "temporada"
    e "4ª" são tokens estranhos pro buscador dele, e SOMAR esses tokens
    estranhos à query piora o ranking em vez de só ser ignorado (testado real:
    a mesma busca SEM o sufixo já trazia o id certo, só que na posição #9,
    fora do limite de 10 pedido - e com o sufixo traduzido pra "4th Season",
    o id certo virou o 1º resultado). Só troca o SUFIXO, mantém a base do
    título intacta - sem esse padrão no título, devolve sem mudança nenhuma."""
    match = re.search(r"^(.*?)\s+(\d+)ª?\s*Temporada\s*$", titulo, re.IGNORECASE)
    if not match:
        return titulo
    base, numero = match.group(1), int(match.group(2))
    sufixo = _SUFIXOS_ORDINAL_EN.get(numero, "th")
    return f"{base} {numero}{sufixo} Season"


def casar_animes_com_mal():
    """Roda 1x por dia (mesmo loop de verificar_novos_lancamentos, ver
    _verificar_e_executar_animes_diario em run.py) - pros animes "tenho_interesse" que ainda
    não têm `mal_anime_id` nem foram marcados `mal_sem_correspondencia` (usuário
    já disse manualmente que nenhum candidato bate, ver ignorar_casamento_mal),
    busca no MAL por título (mal_client.buscar_anime) e:
    - Se o melhor candidato tiver similaridade alta (>= obter_mal_confianca_minima(),
      configurável no Painel) E uma margem clara sobre o segundo colocado
      (>= obter_mal_margem_minima()), casa automaticamente (`mal_anime_id`
      salvo, sem precisar de confirmação manual).
    - Senão, guarda os candidatos em `mal_candidatos` (Painel mostra pro usuário
      escolher manualmente - ver confirmar_casamento_mal/ignorar_casamento_mal).
    Só tenta pra "tenho_interesse" (não faz sentido gastar chamada de API pra
    casar um anime "pendente" que o usuário nem decidiu se quer acompanhar
    ainda). Silencioso se o MAL não estiver configurado (mal_client.esta_configurado())."""
    if not mal_client.esta_configurado():
        return
    animes = _carregar_animes()
    mudou = False
    for chave, registro in animes.items():
        if registro.get("interesse") != "tenho_interesse":
            continue
        if registro.get("mal_anime_id") or registro.get("mal_sem_correspondencia"):
            continue
        titulo_busca = _titulo_para_busca_mal(registro["titulo"])
        candidatos, erro = mal_client.buscar_anime(titulo_busca)
        if erro or not candidatos:
            continue

        pontuados = sorted(
            (dict(c, pontuacao=_similaridade_titulo(titulo_busca, c["title"])) for c in candidatos),
            key=lambda c: c["pontuacao"], reverse=True,
        )
        melhor = pontuados[0]
        segundo = pontuados[1] if len(pontuados) > 1 else None
        margem = melhor["pontuacao"] - segundo["pontuacao"] if segundo else 1.0
        limiar_confianca = obter_mal_confianca_minima() / 100
        limiar_margem = obter_mal_margem_minima() / 100
        if melhor["pontuacao"] >= limiar_confianca and margem >= limiar_margem:
            registro["mal_anime_id"] = melhor["id"]
            registro["mal_num_episodios"] = melhor.get("num_episodes") or None
            registro.pop("mal_candidatos", None)
            print(f" [SISTEMA] 🎬 \"{registro['titulo']}\" casado automaticamente com o MAL: \"{melhor['title']}\" (id {melhor['id']}, confiança {melhor['pontuacao']:.0%}).")
        else:
            registro["mal_candidatos"] = pontuados[:5]
        mudou = True
    if mudou:
        _salvar_animes(animes)


def confirmar_casamento_mal(chave, mal_anime_id, mal_num_episodios=None):
    """Usuário escolheu manualmente no Painel (entre os `mal_candidatos`
    guardados por casar_animes_com_mal) qual anime do MAL corresponde.
    `mal_num_episodios` (total de episódios da obra, se conhecido) é guardado
    junto pra sincronizar_progresso_mal saber quando marcar "Completed" sem
    precisar de outra chamada de API."""
    animes = _carregar_animes()
    if chave in animes:
        animes[chave]["mal_anime_id"] = mal_anime_id
        animes[chave]["mal_num_episodios"] = mal_num_episodios
        animes[chave].pop("mal_candidatos", None)
        animes[chave].pop("mal_sem_correspondencia", None)
        _salvar_animes(animes)


def _extrair_mal_id(entrada):
    """Aceita um link completo (ex.: myanimelist.net/anime/59970/...) ou só o
    número do id, colado no Painel - devolve o id (int) ou None se não
    conseguir reconhecer nenhum dos dois formatos."""
    entrada = (entrada or "").strip()
    match = re.search(r"/anime/(\d+)", entrada)
    if match:
        return int(match.group(1))
    if entrada.isdigit():
        return int(entrada)
    return None


def confirmar_casamento_mal_manual(chave, entrada_usuario):
    """Usuário colou um link (ou id) do MAL direto no Painel - pro caso real
    onde NENHUM candidato da busca automática por título batia (ver
    docs/TODO.md: "Tensei shitara Slime Datta Ken 4ª Temporada" não trazia a
    4ª temporada certa nos top 5 candidatos). Busca o anime de verdade por id
    (mal_client.obter_anime_por_id) só pra confirmar que existe e pegar o
    total de episódios antes de salvar. Devolve (sucesso: bool, mensagem:
    str) pro Painel mostrar o resultado."""
    mal_id = _extrair_mal_id(entrada_usuario)
    if mal_id is None:
        return False, "Não consegui reconhecer um link ou id do MAL nesse texto."
    anime, erro = mal_client.obter_anime_por_id(mal_id)
    if erro or not anime:
        return False, f"Não achei esse anime no MAL: {erro or 'id inexistente'}."
    confirmar_casamento_mal(chave, anime["id"], anime.get("num_episodes"))
    return True, f"Casado com \"{anime['title']}\" (id {anime['id']})."


def ignorar_casamento_mal(chave):
    """Usuário disse no Painel que nenhum dos candidatos bate - marca
    `mal_sem_correspondencia` pra casar_animes_com_mal não tentar de novo
    sozinha todo dia (evita gastar chamada de API repetindo uma busca que o
    usuário já revisou e rejeitou)."""
    animes = _carregar_animes()
    if chave in animes:
        animes[chave].pop("mal_candidatos", None)
        animes[chave]["mal_sem_correspondencia"] = True
        _salvar_animes(animes)


def retentar_casamento_mal(chave):
    """Limpa `mal_sem_correspondencia` (usuário mudou de ideia no Painel) - a
    próxima checagem diária (casar_animes_com_mal) tenta buscar de novo."""
    animes = _carregar_animes()
    if chave in animes:
        animes[chave].pop("mal_sem_correspondencia", None)
        _salvar_animes(animes)


def esta_completo(registro):
    """True se o assistido localmente já bateu (ou passou) o total de
    episódios conhecido pelo MAL (`mal_num_episodios`, só existe pra anime
    já casado - ver casar_animes_com_mal/confirmar_casamento_mal). Sem esse
    total conhecido, nunca conta como completo, mesmo com tudo que já saiu
    assistido - pode vir mais episódio. Usado pela sub-aba "Completo" de
    "Acompanhando" no Painel (2026-08-25, pedido do usuário) e por
    sincronizar_progresso_mal abaixo, pra não duplicar a mesma conta."""
    total_episodios = registro.get("mal_num_episodios")
    if not total_episodios:
        return False
    _, _, assistido = obter_ultimos_episodios_por_status(registro)
    return bool(assistido) and assistido >= total_episodios


def sincronizar_progresso_mal():
    """Fase 2 (docs/TODO.md) - roda no mesmo loop de 5min de
    sincronizar_biblioteca_local (_monitorar_downloads_animes_loop, run.py),
    logo depois. Só age em anime já casado com o MAL (`mal_anime_id`, ver
    casar_animes_com_mal) e só se a sincronização estiver ligada no Painel
    (obter_mal_sync_ativo, brain_store.py - desligada por padrão, mexe na conta
    real do usuário). Compara o maior episódio "assistido" localmente
    (obter_ultimos_episodios_por_status) com o que já foi sincronizado da
    última vez (`mal_ultimo_progresso_sincronizado`) - só chama a API se
    SUBIU, evitando repetir a mesma chamada toda checagem. Marca status
    "completed" se bateu (ou passou) o total de episódios conhecido
    (`mal_num_episodios`, guardado no casamento - None se desconhecido, nesse
    caso nunca marca completed sozinho, só atualiza o progresso). Sempre loga
    a chamada de verdade (sucesso ou erro) - pedido do usuário: nada de mexer
    na conta dele sem avisar."""
    if not mal_client.esta_configurado() or not obter_mal_sync_ativo():
        return
    animes = _carregar_animes()
    mudou = False
    for chave, registro in animes.items():
        mal_anime_id = registro.get("mal_anime_id")
        if not mal_anime_id:
            continue
        _, _, assistido = obter_ultimos_episodios_por_status(registro)
        if assistido is None:
            continue
        ja_sincronizado = registro.get("mal_ultimo_progresso_sincronizado") or 0
        if assistido <= ja_sincronizado:
            continue

        eh_ultimo_episodio = esta_completo(registro)
        status = "completed" if eh_ultimo_episodio else "watching"
        sucesso, erro = mal_client.atualizar_progresso(mal_anime_id, assistido, status=status)
        if sucesso:
            registro["mal_ultimo_progresso_sincronizado"] = assistido
            mudou = True
            print(f" [SISTEMA] 🎬 MAL atualizado: \"{registro['titulo']}\" -> episódio {assistido}{' (Completed)' if eh_ultimo_episodio else ''}.")
        else:
            print(f" [SISTEMA] Erro ao sincronizar \"{registro['titulo']}\" com o MAL: {erro}")
    if mudou:
        _salvar_animes(animes)


# ======================================================
# 📅 CALENDÁRIO/VALIDAÇÃO VIA ANILIST (Fase 1 - docs/TODO.md)
# ======================================================
# 🔥 Limiar configurável no Painel (obter_anilist_limite_atraso_horas,
# brain_store.py, default 18h - dentro da faixa "12-24h" pedida pelo usuário)
# - 2026-08-03, "tudo que foi feito fixo, é bom deixar ser configurável".


def casar_animes_com_anilist():
    """Roda 1x por dia (mesmo loop de casar_animes_com_mal, ver
    _verificar_e_executar_animes_diario em run.py) - pros "tenho_interesse" que já têm
    `mal_anime_id` (ver casar_animes_com_mal) mas ainda não têm `anilist_id`,
    cruza via idMal (anilist_client.buscar_por_mal_id - exato, sem ambiguidade,
    ver docstring de integrations/anilist/anilist_client.py). DEPENDE do
    casamento com o MAL já ter rodado pro anime em questão - se ainda não tem
    mal_anime_id (recém marcado "tenho_interesse", ou casamento MAL pendente de
    confirmação manual), simplesmente espera o próximo ciclo, não tenta cruzar
    por título aqui (evitaria duplicar a mesma lógica fuzzy de
    casar_animes_com_mal só pra AniList)."""
    animes = _carregar_animes()
    mudou = False
    for chave, registro in animes.items():
        if registro.get("interesse") != "tenho_interesse":
            continue
        if registro.get("anilist_id") or registro.get("anilist_sem_correspondencia"):
            continue
        mal_anime_id = registro.get("mal_anime_id")
        if not mal_anime_id:
            continue
        media, erro = anilist_client.buscar_por_mal_id(mal_anime_id)
        if erro:
            print(f" [SISTEMA] Erro ao casar \"{registro['titulo']}\" com a AniList: {erro}")
            continue
        if not media:
            registro["anilist_sem_correspondencia"] = True  # 🔥 tentou e não achou - não fica tentando de novo toda checagem
            mudou = True
            continue
        registro["anilist_id"] = media["id"]
        mudou = True
        print(f" [SISTEMA] 📅 \"{registro['titulo']}\" casado com a AniList (id {media['id']}) - calendário de lançamento disponível.")
    if mudou:
        _salvar_animes(animes)


def avaliar_estado_lancamento(media, ultimo_episodio_site, limite_atraso_horas=None):
    """Máquina de estados pedida pelo usuário (docs/TODO.md, feature 5): compara
    o calendário OFICIAL da AniList (`media`, ver anilist_client.buscar_por_id)
    com `ultimo_episodio_site` (o que o DarkMahou já publicou, scraping real -
    `registro["ultimo_episodio_visto"]`). Devolve uma string:
    - "temporada_encerrada": `status` FINISHED e sem próximo episódio agendado.
    - "disponivel": o site já tem o episódio que deveria estar disponível
      segundo o calendário (nada pendente).
    - "ainda_nao_publicado": o calendário diz que o próximo episódio é
      hoje/já passou da hora, mas ainda dentro da folga de `limite_atraso_horas`.
    - "atrasado": passou da folga configurada e o site continua sem o episódio.
    - None: sem dado suficiente pra avaliar (ex.: AniList sem calendário e
      status não é FINISHED - hiato/anúncio sem data ainda).
    `limite_atraso_horas=None` (padrão) lê o valor configurado no Painel
    (obter_anilist_limite_atraso_horas) na hora - só passe um número aqui pra
    sobrescrever pontualmente (ex.: testes)."""
    if limite_atraso_horas is None:
        limite_atraso_horas = obter_anilist_limite_atraso_horas()
    if not media:
        return None
    proximo = media.get("nextAiringEpisode")
    if media.get("status") == "FINISHED" and not proximo:
        return "temporada_encerrada"
    if not proximo:
        return None

    ultimo_episodio_site = ultimo_episodio_site or 0
    agora = time.time()
    if agora < proximo["airingAt"]:
        # 🔥 ainda não é a hora do próximo - o que já deveria existir é o anterior
        return "disponivel" if ultimo_episodio_site >= proximo["episode"] - 1 else "ainda_nao_publicado"

    if ultimo_episodio_site >= proximo["episode"]:
        return "disponivel"
    atraso_horas = (agora - proximo["airingAt"]) / 3600
    return "atrasado" if atraso_horas >= limite_atraso_horas else "ainda_nao_publicado"


def obter_estados_lancamento_anilist(limite_atraso_horas=None):
    """Pra cada "tenho_interesse" já casado com a AniList (`anilist_id`),
    consulta o calendário atual e avalia o estado (avaliar_estado_lancamento).
    Devolve [(chave, registro, estado, media), ...] - usado pelo loop diário
    (run.py) pra montar as notificações proativas (Fase 1, feature 2 do
    docs/TODO.md - "Frieren lança episódio novo amanhã", "X está atrasado",
    etc.). `estado` None (sem dado suficiente) é incluído mesmo assim - quem
    monta a notificação decide se ignora."""
    resultados = []
    for chave, registro in _carregar_animes().items():
        if registro.get("interesse") != "tenho_interesse":
            continue
        anilist_id = registro.get("anilist_id")
        if not anilist_id:
            continue
        media, erro = anilist_client.buscar_por_id(anilist_id)
        if erro:
            print(f" [SISTEMA] Erro ao consultar calendário AniList de \"{registro['titulo']}\": {erro}")
            continue
        estado = avaliar_estado_lancamento(media, registro.get("ultimo_episodio_visto"), limite_atraso_horas)
        resultados.append((chave, registro, estado, media))
    return resultados


def _formatar_hora(timestamp):
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime("%Hh") if dt.minute == 0 else dt.strftime("%Hh%M")


def formatar_texto_calendario_anilist(resultados):
    """Monta o resumo diário proativo do calendário oficial (features 2 e 3 do
    docs/TODO.md, pedido do usuário: "Frieren lança episódio novo amanhã às
    12h"/"Hoje tem 4 lançamentos que você acompanha"/"X está atrasado") a
    partir de `obter_estados_lancamento_anilist()`. Roda 1x por dia (mesmo
    gate de `verificar_novos_lancamentos`, ver _verificar_e_executar_animes_diario em
    run.py) - "hoje"/"amanhã" mudam naturalmente todo dia (a data de
    lançamento do próximo episódio avança toda semana), então não precisa de
    nenhum controle extra de "já avisei isso" pra essas 2 partes. "Atrasado"
    É esperado repetir dia após dia enquanto o problema persistir (útil,
    não é spam - é o mesmo espírito de lembrete do Modo Jornalista). None se
    não há nada relevante hoje, mesmo padrão de silêncio de
    formatar_texto_pendentes."""
    lancam_hoje, lancam_amanha, atrasados = [], [], []
    hoje = datetime.now().date()
    for _chave, registro, estado, media in resultados:
        proximo = media.get("nextAiringEpisode") if media else None
        if proximo:
            data_lancamento = datetime.fromtimestamp(proximo["airingAt"]).date()
            dias_ate_lancar = (data_lancamento - hoje).days
            if dias_ate_lancar == 0:
                lancam_hoje.append(registro["titulo"])
            elif dias_ate_lancar == 1:
                lancam_amanha.append((registro["titulo"], _formatar_hora(proximo["airingAt"])))
        if estado == "atrasado":
            atrasados.append(registro["titulo"])

    linhas = []
    if lancam_hoje:
        linhas.append(f"📅 Hoje tem lançamento de: {', '.join(lancam_hoje)}.")
    for titulo, hora_txt in lancam_amanha:
        linhas.append(f"🔜 {titulo} lança episódio novo amanhã às {hora_txt}.")
    for titulo in atrasados:
        linhas.append(f"⚠️ {titulo} está atrasado - o calendário oficial já previa episódio novo, o site ainda não publicou.")
    if not linhas:
        return None
    return "📅 Calendário de lançamentos (AniList):\n" + "\n".join(linhas)


# ======================================================
# 🔔 LEMBRETES INTELIGENTES DE ATRASO (feature 3 - docs/TODO.md, sem MAL/AniList)
# ======================================================
# 🔥 Limiares configuráveis no Painel (obter_lembrete_limite_episodios/
# obter_lembrete_limite_dias, brain_store.py, default 3 episódios/7 dias) -
# 2026-08-03, "tudo que foi feito fixo, é bom deixar ser configurável".


def obter_lembretes_atraso(limite_episodios_atraso=None, limite_dias_parado=None):
    """Lembretes calculados só com dado JÁ rastreado localmente - não depende
    de MAL nem AniList (diferente das features 1/2/5 acima), por isso não
    precisa de nenhuma conexão configurada pra funcionar. 2 tipos:
    - "atraso_episodios": `ultimo_episodio_visto` (lançado, scraping do site) -
      último "assistido" - só avisa a partir de `limite_episodios_atraso`
      (evita lembrete por atraso de 1 episódio, normal de quem assiste no fim
      de semana).
    - "dias_parado": dias desde a última vez que ALGUM episódio virou
      "assistido" (`episodios_assistido_em`, ver sincronizar_biblioteca_local)
      - só avisa se ainda houver algo pendente pra assistir (lançado >
      assistido - se já está em dia, "há N dias" não significa nada, o
      usuário só não teve episódio novo pra ver).
    Devolve [(titulo, tipo, valor), ...] - `valor` é o número de episódios ou
    de dias, conforme o tipo. Os 2 limiares lêem o valor configurado no Painel
    quando None (padrão) - só passe um número aqui pra sobrescrever
    pontualmente (ex.: testes)."""
    if limite_episodios_atraso is None:
        limite_episodios_atraso = obter_lembrete_limite_episodios()
    if limite_dias_parado is None:
        limite_dias_parado = obter_lembrete_limite_dias()
    avisos = []
    for _chave, registro in _carregar_animes().items():
        if registro.get("interesse") != "tenho_interesse":
            continue
        lancado, _, assistido = obter_ultimos_episodios_por_status(registro)
        if lancado is None:
            continue
        assistido = assistido or 0

        atraso = lancado - assistido
        if atraso >= limite_episodios_atraso:
            avisos.append((registro["titulo"], "atraso_episodios", atraso))

        datas_assistido = registro.get("episodios_assistido_em", {})
        if datas_assistido and lancado > assistido:
            ultima_data = max(datas_assistido.values())
            dias_parado = (datetime.now().date() - datetime.strptime(ultima_data, "%Y-%m-%d").date()).days
            if dias_parado >= limite_dias_parado:
                avisos.append((registro["titulo"], "dias_parado", dias_parado))
    return avisos


def formatar_texto_lembretes_atraso(avisos):
    """None se não há nada a lembrar hoje - mesmo padrão de silêncio já usado
    em formatar_texto_pendentes/formatar_texto_calendario_anilist."""
    if not avisos:
        return None
    linhas = [
        f"🔔 Você está {valor} episódio(s) atrasado em {titulo}."
        if tipo == "atraso_episodios"
        else f"🔔 Há {valor} dia(s) você não assiste {titulo}."
        for titulo, tipo, valor in avisos
    ]
    return "🔔 Lembretes:\n" + "\n".join(linhas)


def executar_checagem_completa(origem="gaia"):
    """Roda o fluxo INTEIRO de uma vez - scraping de lançamentos, download dos
    pendentes, casamento com MAL/AniList, calendário de lançamentos e
    lembretes de atraso. Extraído do corpo do loop diário
    (_verificar_e_executar_animes_diario, run.py) pra ser reaproveitado também pelo
    disparo manual (botão "🔄 Verificar agora" no Painel, ui/qt_modais/
    animes.py - pedido do usuário 2026-08-07: "comando pra rodar a análise se
    tem anime novo fora do horário combinado"). Só faz o TRABALHO - devolve um
    dict com os textos de notificação já formatados (None quando não há nada
    a avisar naquela categoria) e quantos downloads novos foram disparados;
    quem chama decide como entregar (print/Discord/Painel).

    🔥 `texto_pendentes`/`texto_lembretes` viram None (silenciosos) se o toggle
    correspondente estiver desligado no Painel (obter_anime_notificar_pendentes_ativo/
    obter_anime_lembrete_atraso_ativo, 2026-08-14, pedido do usuário) - o
    ESTADO por trás continua sendo atualizado normalmente
    (verificar_novos_lancamentos sempre roda, por exemplo), só a NOTIFICAÇÃO
    é suprimida.

    🔥 `texto_download_iniciado` (2026-09-05, pedido do usuário: "quero ser
    notificado quando começa a baixar algum episodio") - lista título+episódio
    de cada download disparado NESSA checagem (diferente de
    `texto_baixando_agora`, que lista quem já estava baixando de uma checagem
    anterior).

    🔥 Toda chamada grava uma entrada no histórico de checagens
    (obter_historico_checagens, 2026-09-24) com início/fim, quantos itens a
    home trouxe, quantas páginas de anime foram consultadas, episódios novos
    detectados (e de onde), downloads disparados e os que falharam - mesmo se
    a checagem quebrar no meio (campo `erro`).

    🔥 `origem` (2026-09-24): "gaia" (padrão - chamada pela GAIA via HTTP) ou
    "autonoma" (o próprio MOIRAI rodou com a GAIA fechada, ver
    main._loop_manutencao). O resultado de uma checagem autônoma não tem
    ninguém pra entregar, então os textos acionáveis (downloads iniciados e
    alertas) ficam guardados e são incluídos na PRÓXIMA checagem da GAIA.
    `texto_alertas` junta alerta de site (home vazia, todos os downloads sem
    magnet) e de episódio falhando há mais de `anime_alerta_falha_horas`.
    Um lock impede 2 checagens simultâneas (GAIA + autônoma) de dispararem o
    mesmo download duas vezes."""
    with lock_estado_animes:
        return _executar_checagem_completa(origem)


# 🔥 Lock do estado dos animes (2026-09-24) - a checagem completa e os 2 loops
# de main.py (downloads a cada ~30s, manutenção a cada 5min) rodam em threads
# diferentes e todos fazem carregar -> alterar -> salvar no mesmo JSON; sem
# isso, uma escrita podia apagar a outra. RLock porque a checagem chama
# funções que também podem ser chamadas soltas.
lock_estado_animes = threading.RLock()
ARQUIVO_RESULTADOS_NAO_ENTREGUES = caminho_dados("anime_tracker_resultados_nao_entregues.json")


def _executar_checagem_completa(origem):
    inicio = datetime.now()
    relatorio = {}
    itens_baixados, falhas_download, erro = [], [], None
    try:
        pendentes = verificar_novos_lancamentos(relatorio)
        texto_pendentes = formatar_texto_pendentes(pendentes) if obter_anime_notificar_pendentes_ativo() else None
        disparados, itens_baixados, falhas_download = processar_downloads_pendentes()
    except Exception as e:
        erro = str(e)
        raise
    finally:
        _registrar_checagem({
            "inicio": inicio.strftime("%Y-%m-%d %H:%M:%S"),
            "fim": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "origem": origem,
            **relatorio,
            "downloads_disparados": [{"titulo": t, "episodio": n} for t, n in itens_baixados],
            "downloads_com_falha": _detalhar_falhas(falhas_download),
            "erro": erro,
        })
    texto_download_iniciado = formatar_texto_download_iniciado(itens_baixados)
    texto_alertas = formatar_texto_alertas(
        _alertas_de_site(relatorio, itens_baixados, falhas_download) + _coletar_alertas_falha_persistente()
    )
    backfill_temporadas_estreia()
    casar_animes_com_mal()
    casar_animes_com_anilist()
    estados_anilist = obter_estados_lancamento_anilist()
    texto_calendario = formatar_texto_calendario_anilist(estados_anilist)
    avisos_atraso = obter_lembretes_atraso() if obter_anime_lembrete_atraso_ativo() else []
    texto_lembretes = formatar_texto_lembretes_atraso(avisos_atraso)
    texto_baixando_agora = formatar_texto_baixando_agora(obter_animes_com_download_ativo())
    resultado = {
        "texto_pendentes": texto_pendentes,
        "disparados": disparados,
        "texto_download_iniciado": texto_download_iniciado,
        "texto_alertas": texto_alertas,
        "texto_calendario": texto_calendario,
        "texto_lembretes": texto_lembretes,
        "texto_baixando_agora": texto_baixando_agora,
    }
    if origem == "autonoma":
        _guardar_resultado_nao_entregue(inicio, resultado)
    else:
        _incluir_resultados_nao_entregues(resultado)
    return resultado


def _detalhar_falhas(falhas_download):
    """[(titulo, numero)] -> [{"titulo", "episodio", "motivo"}] - motivo lido
    de `episodios_falha_download` (gravado por _registrar_falha_download)."""
    por_titulo = {r["titulo"]: r for r in _carregar_animes().values()}
    detalhes = []
    for titulo, numero in falhas_download:
        falha = por_titulo.get(titulo, {}).get("episodios_falha_download", {}).get(str(numero), {})
        detalhes.append({"titulo": titulo, "episodio": numero, "motivo": falha.get("motivo")})
    return detalhes


def _alertas_de_site(relatorio, itens_baixados, falhas_download):
    """Sinais de que o DarkMahou mudou (2026-09-24: a mudança de charset de
    23/09 passou 1 dia inteiro sem ninguém notar). Home vazia, ou 2+
    downloads tentados na checagem e nenhum deu certo."""
    alertas = []
    if relatorio.get("itens_home", 0) == 0:
        alertas.append("A home do DarkMahou veio vazia - o site pode estar fora do ar ou ter mudado de layout.")
    if len(falhas_download) >= 2 and not itens_baixados:
        alertas.append(
            f"Nenhum dos {len(falhas_download)} downloads tentados deu certo - "
            "confira se a página dos animes mudou de estrutura."
        )
    return alertas


def _coletar_alertas_falha_persistente():
    """Episódios em `episodios_falha_download` há mais de
    `anime_alerta_falha_horas` que ainda não foram alertados - avisa 1x só
    por episódio (marca `alertado`). Continua sendo retentado normalmente."""
    limite = timedelta(hours=obter_anime_alerta_falha_horas())
    agora = datetime.now()
    animes = _carregar_animes()
    alertas = []
    mudou = False
    for registro in animes.values():
        if registro.get("interesse") != "tenho_interesse":
            continue
        # Especial ("especial-1") depois dos numerados - int() direto quebrava a coleta inteira.
        falhas = registro.get("episodios_falha_download", {}).items()
        for numero, falha in sorted(falhas, key=lambda par: (not par[0].isdigit(), int(par[0]) if par[0].isdigit() else par[0])):
            if falha.get("alertado"):
                continue
            try:
                desde = datetime.strptime(falha["desde"], "%Y-%m-%d %H:%M")
            except (KeyError, ValueError):
                continue
            if agora - desde >= limite:
                horas = int((agora - desde).total_seconds() // 3600)
                alertas.append(f"{registro['titulo']} Episódio {_rotulo_episodio(registro, numero)} falha há {horas}h ({falha.get('motivo') or 'motivo desconhecido'}).")
                falha["alertado"] = True
                mudou = True
    if mudou:
        _salvar_animes(animes)
    return alertas


def formatar_texto_alertas(alertas):
    if not alertas:
        return None
    return "⚠️ Alertas do Assistente de Animes:\n" + "\n".join(f"- {a}" for a in alertas)


def _carregar_resultados_nao_entregues():
    try:
        with open(ARQUIVO_RESULTADOS_NAO_ENTREGUES, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _salvar_resultados_nao_entregues(lista):
    os.makedirs(os.path.dirname(ARQUIVO_RESULTADOS_NAO_ENTREGUES), exist_ok=True)
    with open(ARQUIVO_RESULTADOS_NAO_ENTREGUES, "w", encoding="utf-8") as f:
        json.dump(lista, f, indent=4, ensure_ascii=False)


def _guardar_resultado_nao_entregue(inicio, resultado):
    textos = {k: resultado[k] for k in ("texto_download_iniciado", "texto_alertas") if resultado.get(k)}
    if not textos:
        return
    lista = _carregar_resultados_nao_entregues()
    lista.append({"quando": inicio.strftime("%d/%m %H:%M"), **textos})
    _salvar_resultados_nao_entregues(lista)


def _incluir_resultados_nao_entregues(resultado):
    """Prefixa os textos guardados pelas checagens autônomas nos campos
    correspondentes do resultado desta checagem e limpa o arquivo."""
    lista = _carregar_resultados_nao_entregues()
    if not lista:
        return
    for campo in ("texto_download_iniciado", "texto_alertas"):
        blocos = [f"(checagem de {item['quando']}, com a GAIA fechada)\n{item[campo]}" for item in lista if item.get(campo)]
        if resultado.get(campo):
            blocos.append(resultado[campo])
        resultado[campo] = "\n\n".join(blocos) if blocos else None
    _salvar_resultados_nao_entregues([])


def horas_desde_ultima_checagem():
    """Horas desde o INÍCIO da checagem mais recente no histórico (qualquer
    origem) - None se nunca houve checagem registrada."""
    historico = _carregar_historico_checagens()
    if not historico:
        return None
    try:
        ultima = datetime.strptime(historico[-1]["inicio"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, ValueError):
        return None
    return (datetime.now() - ultima).total_seconds() / 3600

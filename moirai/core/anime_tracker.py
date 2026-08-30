"""Assistente de Animes (2026-08-02, pedido do usuário) - acompanha os lançamentos de
episódios em https://darkmahou.io/ (scraping puro, sem API oficial - o site não tem
uma), avisa 1x por dia (ver _verificar_e_executar_animes_diario, run.py) quais animes ganharam
episódio novo, deixa o usuário marcar interesse/desinteresse por anime (ver
obter_animes_rastreados/marcar_interesse, usado pelo Painel - ui/qt_modais/animes.py),
e baixa automaticamente (via magnet, qBittorrent) os episódios dos animes marcados
"tenho_interesse", priorizando 1080p HEVC.

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
3. `verificar_downloads_em_andamento()` - roda mais frequente (loop próprio, minutos)
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

import json
import os
import re
import shutil
import subprocess
import threading
import time
import winreg
from datetime import datetime
from difflib import SequenceMatcher

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
)

URL_BASE = "https://darkmahou.io"
ARQUIVO_ANIMES = "data/anime_tracker_animes.json"
ARQUIVO_CHECAGEM_DIARIA = "data/anime_tracker_checagem_diaria.json"
CATEGORIA_QBITTORRENT = "gaia-animes"

# 🔥 Site protegido por Cloudflare mas sem desafio JS de verdade (testado 2026-08-02) -
# um User-Agent de navegador comum já basta, sem precisar de navegador automatizado.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_TIMEOUT_REQUEST = 20

EXTENSOES_VIDEO = (".mkv", ".mp4", ".avi")
PASTA_CAPAS = "data/anime_tracker_capas"


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
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT_REQUEST)
        resp.raise_for_status()
    except Exception as e:
        print(f" [SISTEMA] Erro ao buscar temporada de estreia ({url}): {e}")
        return False, None
    return True, _extrair_temporada_estreia(BeautifulSoup(resp.text, "html.parser"))


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
        resp = requests.get(URL_BASE, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT_REQUEST)
        resp.raise_for_status()
    except Exception as e:
        print(f" [SISTEMA] Erro ao acessar DarkMahou (lançamentos): {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
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


def verificar_novos_lancamentos():
    """Roda 1x por dia (ver _verificar_e_executar_animes_diario, run.py). Atualiza o estado de
    CADA anime visto na home com o último episódio (mesmo os já marcados
    interesse/sem interesse - o estado precisa continuar atual pra
    processar_downloads_pendentes saber se tem episódio novo). Devolve só os
    "pendente" (nem marcados com nem sem interesse) - pedido do usuário: esses
    precisam continuar sendo informados TODO dia até serem marcados."""
    animes = _carregar_animes()
    pendentes = []
    for item in listar_ultimos_lancamentos():
        chave = _chave_de_url(item["url"])
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
            if item["episodio"] is not None:
                registro["ultimo_episodio_visto"] = item["episodio"]
        if registro.get("interesse", "pendente") == "pendente":
            pendentes.append((chave, registro))
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
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT_REQUEST)
        resp.raise_for_status()
    except Exception as e:
        return None, f"Erro ao acessar a página do anime: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")
    titulo_tag = soup.find("h1")
    if not titulo_tag:
        return None, "Não achei o título na página - a estrutura do site pode ter mudado."
    titulo = titulo_tag.get_text(strip=True)

    numeros = [
        _numero_episodio_de_texto(bloco.find("h3").get_text(strip=True))
        for bloco in soup.find_all("div", class_="soraddl") if bloco.find("h3")
    ]
    numeros = [n for n in numeros if n is not None]
    ultimo_episodio = max(numeros) if numeros else None

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
def _escolher_melhor_magnet(opcoes):
    """`opcoes`: [(rotulo, magnet), ...] da primeira linha (legendado) da tabela de
    download. Prioriza 1080p+HEVC > 1080p (qualquer encoder) > primeira opção
    disponível, nessa ordem - pedido do usuário ("priorizando os 1080p HEVC")."""
    if not opcoes:
        return None

    def pontuar(rotulo):
        rotulo_min = rotulo.lower()
        if "1080p" in rotulo_min and "hevc" in rotulo_min:
            return 2
        if "1080p" in rotulo_min:
            return 1
        return 0

    return max(opcoes, key=lambda par: pontuar(par[0]))[1]


def _extrair_opcoes_download(url_anime, numero_episodio):
    """Baixa a página do anime e devolve [(rotulo, magnet), ...] da PRIMEIRA linha
    (legendado) do bloco do episódio pedido. Lista vazia se a página/episódio não
    for encontrado - nunca lança exceção."""
    try:
        resp = requests.get(url_anime, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT_REQUEST)
        resp.raise_for_status()
    except Exception as e:
        print(f" [SISTEMA] Erro ao acessar página do anime ({url_anime}): {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    # 🔥 Prefixo, não igualdade exata (2026-08-05, bug real encontrado - o
    # último episódio de uma temporada às vezes vem com um sufixo, ex.:
    # "Episódio 13 Final" - igualdade exata contra "Episódio 13" nunca batia,
    # o download do episódio final simplesmente nunca era encontrado). `\b`
    # depois do número evita falso positivo de "Episódio 13" casar com
    # "Episódio 130" (ou similar).
    padrao_alvo = re.compile(rf"^Epis[oó]dio\s+0*{numero_episodio}\b", re.IGNORECASE)
    for bloco in soup.find_all("div", class_="soraddl"):
        titulo_bloco = bloco.find("h3")
        if not titulo_bloco or not padrao_alvo.match(titulo_bloco.get_text(strip=True)):
            continue
        primeira_linha = bloco.find("tr")
        if not primeira_linha:
            return []
        links_magnet = primeira_linha.find_all("a", href=re.compile(r"^magnet:"))
        return [(a.get_text(strip=True), a["href"]) for a in links_magnet]
    return []


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
    colisão de nome entre temporadas documentado no TODO.md). Reconhece "Nª
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


def baixar_episodio(chave, registro, numero_episodio):
    """Extrai o magnet do episódio (1080p HEVC de preferência) e manda pro
    qBittorrent - `save_path` é a pasta de downloads configurada no Painel
    (obter_anime_pasta_downloads, default "E:\\Downloads" - a mesma pasta onde o
    usuário já mantém episódio baixado e ainda não assistido, sem subpasta por
    anime: o nome do arquivo já sai único via _sanitizar_nome_arquivo/renomeação em
    verificar_downloads_em_andamento). Registra o hash em `downloads_em_andamento`
    do próprio anime, pra verificar_downloads_em_andamento() saber o que
    acompanhar. Não faz nada (devolve False) se o episódio já foi baixado (ou
    assistido), já está baixando, ou se não achou nenhum magnet - idempotente,
    seguro de chamar toda vez que processar_downloads_pendentes rodar."""
    if registro.get("episodios", {}).get(str(numero_episodio)) in ("baixado", "assistido"):
        return False
    if str(numero_episodio) in registro.get("downloads_em_andamento", {}):
        return False

    opcoes = _extrair_opcoes_download(registro["url"], numero_episodio)
    magnet = _escolher_melhor_magnet(opcoes)
    if not magnet:
        print(f" [SISTEMA] Nenhum magnet encontrado pra {registro['titulo']} Episódio {numero_episodio}.")
        return False

    match_hash = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
    hash_torrent = match_hash.group(1).lower() if match_hash else None
    if not hash_torrent:
        print(f" [SISTEMA] Magnet de {registro['titulo']} Episódio {numero_episodio} sem hash reconhecível - pulando.")
        return False

    pasta_destino = obter_anime_pasta_downloads()
    os.makedirs(pasta_destino, exist_ok=True)
    import qbittorrentapi
    try:
        cliente = _cliente_qbittorrent()
        cliente.torrents_add(urls=magnet, save_path=pasta_destino, category=CATEGORIA_QBITTORRENT)
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
        print(f" [SISTEMA] 🎬 {registro['titulo']} Episódio {numero_episodio} já existe no qBittorrent (hash conhecido - provavelmente baixado por fora do fluxo automático) - não vou tentar de novo. Confira manualmente se o arquivo está na sua biblioteca.")
        animes = _carregar_animes()
        if chave in animes:
            animes[chave].setdefault("episodios", {})[str(numero_episodio)] = "conflito_qbittorrent"
            _salvar_animes(animes)
        return False
    except Exception as e:
        print(f" [SISTEMA] Erro ao mandar {registro['titulo']} Episódio {numero_episodio} pro qBittorrent: {e}")
        return False

    animes = _carregar_animes()
    if chave in animes:
        animes[chave].setdefault("downloads_em_andamento", {})[str(numero_episodio)] = {
            "hash": hash_torrent, "pasta": pasta_destino,
        }
        _salvar_animes(animes)
    print(f" [SISTEMA] 🎬 Baixando {registro['titulo']} Episódio {numero_episodio} ({[r for r, m in opcoes if m == magnet][0] if opcoes else '?'})...")
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
    padrão certo é presumir que quer assistir desde o início)."""
    ultimo_lancado = registro.get("ultimo_episodio_visto")
    if ultimo_lancado is None:
        return []
    numeros_conhecidos = [int(n) for n in registro.get("episodios", {})]
    numeros_conhecidos += [int(n) for n in registro.get("downloads_em_andamento", {})]
    if not numeros_conhecidos:
        return list(range(1, ultimo_lancado + 1))
    maior_conhecido = max(numeros_conhecidos)
    return list(range(maior_conhecido + 1, ultimo_lancado + 1))


def _baixar_pendentes_do_registro(chave, registro):
    """Baixa (fechando o gap, ver _episodios_a_baixar) todo episódio pendente
    de UM anime específico - usado tanto pelo loop diário
    (processar_downloads_pendentes, todos os "tenho_interesse") quanto pelo
    disparo imediato ao marcar interesse (baixar_pendentes_de, só esse
    anime). Devolve quantos downloads novos foram disparados."""
    disparados = 0
    for numero_episodio in _episodios_a_baixar(registro):
        if baixar_episodio(chave, registro, numero_episodio):
            disparados += 1
    return disparados


def processar_downloads_pendentes():
    """Pros animes marcados "tenho_interesse", baixa qualquer episódio entre o
    maior já conhecido e o último lançado que ainda não foi baixado nem está
    baixando (ver _episodios_a_baixar - fecha gaps, não só "o último"). Devolve
    quantos downloads novos foram disparados (só informativo pro log de quem
    chama)."""
    if not qbittorrent_configurado():
        return 0
    disparados = 0
    for chave, registro in _carregar_animes().items():
        if registro.get("interesse") != "tenho_interesse":
            continue
        disparados += _baixar_pendentes_do_registro(chave, registro)
    return disparados


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
    return _baixar_pendentes_do_registro(chave, registro)


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
    disparados = 0
    for numero_episodio in numeros:
        if baixar_episodio(chave, registro, numero_episodio):
            disparados += 1
    return disparados


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
    Downloads Ativos" só quando ela tem conteúdo de verdade pra mostrar."""
    animes = _carregar_animes()
    return [
        registro["titulo"]
        for registro in animes.values()
        if registro.get("downloads_em_andamento")
    ]


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
    pendentes = [
        (chave, str(ep), info)
        for chave, registro in animes.items()
        for ep, info in registro.get("downloads_em_andamento", {}).items()
    ]
    if not pendentes:
        return 0

    try:
        cliente = _cliente_qbittorrent()
    except Exception as e:
        print(f" [SISTEMA] Erro ao conectar no qBittorrent pra checar downloads: {e}")
        return 0

    concluidos = 0
    for chave, numero_episodio_str, info in pendentes:
        try:
            torrents = cliente.torrents_info(torrent_hashes=info["hash"])
        except Exception as e:
            print(f" [SISTEMA] Erro ao consultar torrent {info['hash']}: {e}")
            continue
        if not torrents or torrents[0].progress < 1.0:
            continue

        registro = animes[chave]
        agora = datetime.now().strftime("%Y-%m-%d %H:%M")
        caminho_conteudo = torrents[0].content_path or info["pasta"]
        arquivo_video = _maior_arquivo_video(caminho_conteudo)
        if arquivo_video:
            extensao = os.path.splitext(arquivo_video)[1]
            numero_temporada = _detectar_numero_temporada(registro["titulo"])
            nome_novo = f"{_sanitizar_nome_arquivo(registro['titulo'])} - S{numero_temporada:02d}E{int(numero_episodio_str):02d}{extensao}"
            caminho_novo = os.path.join(os.path.dirname(arquivo_video), nome_novo)
            try:
                if not os.path.exists(caminho_novo):
                    os.rename(arquivo_video, caminho_novo)
                    registro.setdefault("episodios_renomeado_em", {})[numero_episodio_str] = agora
                else:
                    registro.setdefault("episodios_erro_renomear", {})[numero_episodio_str] = (
                        f"{agora}: destino já existia ({nome_novo}) - arquivo original não foi mexido"
                    )
            except OSError as e:
                print(f" [SISTEMA] Erro ao renomear {arquivo_video}: {e}")
                registro.setdefault("episodios_erro_renomear", {})[numero_episodio_str] = f"{agora}: {e}"
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

        registro.setdefault("episodios", {})[numero_episodio_str] = "baixado"
        registro.setdefault("episodios_baixado_em", {})[numero_episodio_str] = agora
        registro.get("downloads_em_andamento", {}).pop(numero_episodio_str, None)
        concluidos += 1
        print(f" [SISTEMA] 🎬 Download concluído: {registro['titulo']} Episódio {numero_episodio_str}.")
        try:
            cliente.torrents_delete(delete_files=False, torrent_hashes=info["hash"])
            registro.setdefault("episodios_removido_qbittorrent_em", {})[numero_episodio_str] = agora
        except Exception as e:
            print(f" [SISTEMA] Erro ao remover {registro['titulo']} Episódio {numero_episodio_str} da lista do qBittorrent: {e}")

    if concluidos:
        _salvar_animes(animes)
    return concluidos


# ======================================================
# 📚 BIBLIOTECA LOCAL (baixado vs. assistido)
# ======================================================
# 🔥 Temporada ("S\d+") opcional no padrão (2026-08-03, pedido do usuário -
# incluir temporada no nome renomeado) - continua reconhecendo arquivos já
# renomeados ANTES dessa mudança (só "Título - E08.ext", sem temporada).
_PADRAO_NOME_ARQUIVO = re.compile(r"^(.*) - (?:S\d+)?E(\d+)\.\w+$")


def _mapear_arquivos_por_titulo(pasta):
    """Varre `pasta` (recursivo - o usuário pode organizar em subpastas) por
    arquivos de vídeo no padrão "{Título sanitizado} - S{SS}E{NN}.ext" (o
    mesmo que verificar_downloads_em_andamento produz - temporada opcional,
    reconhece também o padrão antigo sem temporada) e devolve
    {titulo_sanitizado: {numero_episodio: True}}. Ignora qualquer arquivo fora
    do padrão (o usuário pode ter outras coisas nas mesmas pastas, não é
    problema nosso)."""
    resultado = {}
    if not pasta or not os.path.isdir(pasta):
        return resultado
    for raiz, _, arquivos in os.walk(pasta):
        for nome in arquivos:
            if not nome.lower().endswith(EXTENSOES_VIDEO):
                continue
            match = _PADRAO_NOME_ARQUIVO.match(nome)
            if not match:
                continue
            titulo_sanitizado, numero_episodio = match.group(1), int(match.group(2))
            resultado.setdefault(titulo_sanitizado, set()).add(numero_episodio)
    return resultado


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
    abre o resultado via `assistir_e_monitorar` (abaixo)."""
    registro = _carregar_animes().get(chave)
    if not registro:
        return None, None
    prefixo = _sanitizar_nome_arquivo(registro["titulo"])
    pasta = obter_anime_pasta_downloads()
    if not pasta or not os.path.isdir(pasta):
        return None, None
    candidatos = {}
    for raiz, _, arquivos in os.walk(pasta):
        for nome in arquivos:
            if not nome.lower().endswith(EXTENSOES_VIDEO):
                continue
            match = _PADRAO_NOME_ARQUIVO.match(nome)
            if not match or match.group(1) != prefixo:
                continue
            candidatos[int(match.group(2))] = os.path.join(raiz, nome)
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
    estado local ficava instantâneo mas o MAL só pegava no próximo ciclo)."""
    registro = _carregar_animes().get(chave, {})

    pasta_assistidos = obter_anime_pasta_assistidos()
    try:
        if pasta_assistidos and os.path.isfile(caminho_arquivo):
            os.makedirs(pasta_assistidos, exist_ok=True)
            destino = os.path.join(pasta_assistidos, os.path.basename(caminho_arquivo))
            if not os.path.exists(destino):
                shutil.move(caminho_arquivo, destino)
    except OSError as e:
        print(f" [SISTEMA] Erro ao mover episódio pra pasta de assistidos: {e}")
    sincronizar_biblioteca_local()
    sincronizar_progresso_mal()
    print(f" [SISTEMA] 🎬 Episódio {numero_episodio} marcado como assistido (movido pra pasta de assistidos sozinha).")
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
    assistidos = _mapear_arquivos_por_titulo(obter_anime_pasta_assistidos())

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
        resp = requests.get(url_anime, headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT_REQUEST)
        resp.raise_for_status()
    except Exception as e:
        print(f" [SISTEMA] Erro ao acessar página do anime ({url_anime}): {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    resultado = {}
    for bloco in soup.find_all("div", class_="soraddl"):
        titulo_bloco = bloco.find("h3")
        if not titulo_bloco:
            continue
        numero_episodio = _numero_episodio_de_texto(titulo_bloco.get_text(strip=True))
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


def _renomear_arquivo_via_api_qbittorrent(cliente, torrent, arquivo_video, nome_novo):
    """Renomeia o arquivo de conteúdo de um torrent chamando
    `torrents_rename_file` da própria API do qBittorrent, em vez de
    `os.rename` cru (2026-08-08, pedido do usuário) - assim o qBittorrent
    continua sabendo onde o arquivo está e pode seguir semeando normalmente
    depois do rename, sem virar "missingFiles" (bug real visto na biblioteca
    do usuário - vários torrents já "missingFiles" provavelmente por causa
    de rename manual feito assim antes). Precisa do caminho RELATIVO do
    arquivo dentro do torrent (não o caminho absoluto no disco) - calculado a
    partir de `torrent.save_path`, funciona tanto pra torrent de arquivo
    único quanto pra torrent com pasta (multi-arquivo)."""
    caminho_relativo_atual = os.path.relpath(arquivo_video, torrent.save_path)
    pasta_relativa = os.path.dirname(caminho_relativo_atual)
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


def executar_checagem_completa():
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
    é suprimida."""
    pendentes = verificar_novos_lancamentos()
    texto_pendentes = formatar_texto_pendentes(pendentes) if obter_anime_notificar_pendentes_ativo() else None
    disparados = processar_downloads_pendentes()
    backfill_temporadas_estreia()
    casar_animes_com_mal()
    casar_animes_com_anilist()
    estados_anilist = obter_estados_lancamento_anilist()
    texto_calendario = formatar_texto_calendario_anilist(estados_anilist)
    avisos_atraso = obter_lembretes_atraso() if obter_anime_lembrete_atraso_ativo() else []
    texto_lembretes = formatar_texto_lembretes_atraso(avisos_atraso)
    return {
        "texto_pendentes": texto_pendentes,
        "disparados": disparados,
        "texto_calendario": texto_calendario,
        "texto_lembretes": texto_lembretes,
    }

# -*- coding: utf-8 -*-
"""Séries e filmes do TorrentFilmesHD (2026-10-17, pedido do usuário: "quero
adicionar uma segunda fonte no moirai" - DarkMahou continua sendo a fonte dos
animes e esta passa a ser a de séries e filmes, com o mesmo processo).

Mesmo fluxo do `anime_tracker`: cada título que aparece na home entra como
"pendente"; 👍 ("tenho_interesse") baixa sozinho (série: todos os episódios
da temporada e os que saírem depois; filme: uma vez); 👎 ("sem_interesse")
para de mostrar. Cada temporada de série tem página (e chave) própria no
site, igual às temporadas de anime no DarkMahou.

Escolha da versão (pedido do usuário: "Prioridade dual, depois dublado,
sempre na melhor qualidade disponivel"): áudio primeiro (dual/multi >
dublado > o resto), resolução depois. Gravação de cinema (CAM/TS/telesync)
nunca é baixada sozinha: o filme fica esperando a página ganhar uma versão
de verdade.

Estado em `data/midia_tracker_titulos.json`, separado do dos animes, com
lock próprio (`lock_estado_midia`) - a checagem de séries e filmes não
segura a fila dos animes nem o contrário."""
import base64
import html as html_lib
import json
import os
import re
import shutil
import threading
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from moirai.config import (
    obter_anime_download_travado_horas, obter_midia_pasta_filmes, obter_midia_pasta_series,
)
from moirai.core import anime_tracker as at
from moirai.paths import caminho_dados

URL_BASE = "https://www.torrentfilmeshds.org"
ARQUIVO_TITULOS = caminho_dados("midia_tracker_titulos.json")
CATEGORIA_QBITTORRENT = "gaia-midia"
# Prefixo das capas no cache do anime_tracker (rota /anime/capa/<chave>), pra
# slug de filme nunca sobrescrever a capa de um anime com o mesmo slug.
PREFIXO_CAPA = "midia-"
IDENT_FILME = "filme"
IDENT_PACOTE = "pacote"
_SEGUNDOS_CACHE_HTML = 120
_HORAS_TRAVADO_SEM_SEED = 2

lock_estado_midia = threading.RLock()
_lock_arquivo = threading.Lock()
_cache_html = {}


# ======================================================
# 📦 ESTADO PERSISTIDO
# ======================================================
def _carregar():
    with _lock_arquivo:
        try:
            with open(ARQUIVO_TITULOS, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def _salvar(titulos):
    os.makedirs(os.path.dirname(ARQUIVO_TITULOS), exist_ok=True)
    temporario = ARQUIVO_TITULOS + ".tmp"
    with _lock_arquivo:
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump(titulos, f, indent=4, ensure_ascii=False)
        os.replace(temporario, ARQUIVO_TITULOS)


def obter_titulos_rastreados():
    """[(chave, registro), ...] - tela de Séries e Filmes da GAIA."""
    return list(_carregar().items())


_CAMPOS_MINIMOS_SEM_INTERESSE = ("tipo", "titulo", "url", "interesse")


def marcar_interesse(chave, interesse):
    """Mesmo contrato de anime_tracker.marcar_interesse: "sem_interesse"
    enxuga o registro, que só precisa existir pra não voltar a Pendentes."""
    with lock_estado_midia:
        titulos = _carregar()
        if chave not in titulos:
            return
        titulos[chave]["interesse"] = interesse
        if interesse == "sem_interesse":
            registro = titulos[chave]
            titulos[chave] = {c: registro[c] for c in _CAMPOS_MINIMOS_SEM_INTERESSE if c in registro}
        _salvar(titulos)


def remover_titulo(chave):
    with lock_estado_midia:
        titulos = _carregar()
        if titulos.pop(chave, None) is not None:
            _salvar(titulos)


# ======================================================
# 🔎 SCRAPING
# ======================================================
def _obter_html(url):
    agora = datetime.now()
    em_cache = _cache_html.get(url)
    if em_cache and (agora - em_cache[0]).total_seconds() < _SEGUNDOS_CACHE_HTML:
        return em_cache[1]
    resp = requests.get(url, headers={"User-Agent": at.USER_AGENT, "Referer": URL_BASE + "/"}, timeout=at._TIMEOUT_REQUEST)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    _cache_html[url] = (agora, resp.text)
    return resp.text


def _chave_de_url(url):
    return url.rstrip("/").rsplit("/", 1)[-1]


_PADRAO_SLUG_SERIE = re.compile(r"-\d+a-temporada-")


def tipo_do_slug(chave):
    """Série tem "-Na-temporada-" no slug (`slow-horses-6a-temporada-2026`);
    o resto é filme. Vale para a home e para o link colado à mão."""
    return "serie" if _PADRAO_SLUG_SERIE.search(chave) else "filme"


def listar_lancamentos_home():
    """Cada `article.item` das seções da home (Últimos Filmes, Últimas
    Séries e os destaques de cada um), sem repetir título. Lista vazia em
    qualquer falha, mesmo espírito de anime_tracker.listar_ultimos_lancamentos."""
    try:
        html = _obter_html(URL_BASE + "/")
    except Exception as e:
        print(f" [SISTEMA] Erro ao acessar o TorrentFilmesHD (home): {e}")
        return []
    soup = BeautifulSoup(html, "html.parser")
    itens, vistos = [], set()
    for artigo in soup.select("section.filmes-section article.item"):
        link = artigo.find("a", href=True)
        if not link:
            continue
        url = urljoin(URL_BASE, link["href"])
        chave = _chave_de_url(url)
        if chave in vistos:
            continue
        vistos.add(chave)
        titulo = artigo.select_one("div.titulo span")
        capa = artigo.find("img", src=True)
        itens.append({
            "chave": chave,
            "url": url,
            "titulo": titulo.get_text(strip=True) if titulo else link.get("title", chave),
            "capa_url": capa["src"] if capa else None,
            "tipo": tipo_do_slug(chave),
        })
    if not itens:
        print(" [SISTEMA] TorrentFilmesHD: nenhum título na home - o site pode ter mudado de layout.")
    return itens


_PADRAO_SUFIXO_TITULO = re.compile(r"\s+Torrent\b.*$", re.IGNORECASE)
# "Ep.01", "Episódio 10 Final", "S01E03" e, num torrent só, "Ep. 01 e 02",
# "Episódios 01 a 03", "Ep.01/03", "S01E01-02" (caso real VisionQuest, 2026-10-17).
_PADRAO_EPISODIO = re.compile(
    r"(?:\bEp\.?|Epis[oó]dios?|\bS\d{1,2}E)\s*0*(\d{1,3})\b"
    r"(?:\s*(e|a|ao|&|,|-|~|/)\s*E?0*(\d{1,3})\b)?", re.IGNORECASE)
_SEPARADORES_LISTA = ("e", "&", ",")
_PADRAO_TEMPORADA_ROTULO = re.compile(r"\bS(\d{1,2})(?=[.E\s|]|$)", re.IGNORECASE)
_PADRAO_TEMPORADA_TITULO = re.compile(r"(\d+)ª\s*Temporada", re.IGNORECASE)
_PADRAO_CAM = re.compile(r"\bCAM\b|CAMRip|HDCAM|\bTS\b|HDTS|tele\s*sync|tene\s*sync", re.IGNORECASE)
_PADRAO_DUAL = re.compile(r"dual|multi[- ]?[áa]udio", re.IGNORECASE)
_PADRAO_DUBLADO = re.compile(r"dublado|portugu[eê]s|nacional", re.IGNORECASE)
_PADRAO_TAMANHO = re.compile(r"([\d.,]+)\s*(GB|MB)", re.IGNORECASE)


def _resolucao(rotulo):
    if re.search(r"2160p|\b4K\b|UHD", rotulo, re.IGNORECASE):
        return 2160
    m = re.search(r"(\d{3,4})p\b", rotulo, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _nivel_audio(rotulo):
    """2 = dual/multi áudio, 1 = dublado, 0 = o resto (legendado, inglês,
    multi-legendas). Ordem pedida pelo usuário."""
    if _PADRAO_DUAL.search(rotulo):
        return 2
    if _PADRAO_DUBLADO.search(rotulo):
        return 1
    return 0


def interpretar_rotulo(rotulo):
    """Rótulo do botão de download ("S06.1080p | MKV | Ep.01 | Dual Áudio",
    "S02.1080p | Episódio 10 Final | Multi Áudio", "S01.1080p | 6.41 GB |
    Dual Áudio", "CAMRip | 5.11 GB | Inglês") -> dict com episódio (None se
    for filme ou pacote da temporada), temporada, resolução, nível de
    áudio, se é gravação de cinema e tamanho em GB."""
    m_ep = _PADRAO_EPISODIO.search(rotulo)
    episodios = []
    if m_ep:
        inicio = int(m_ep.group(1))
        fim = int(m_ep.group(3)) if m_ep.group(3) else inicio
        if m_ep.group(3) and m_ep.group(2).lower() in _SEPARADORES_LISTA:
            episodios = sorted({inicio, fim})
        else:
            episodios = list(range(inicio, max(inicio, fim) + 1))
    m_temp = _PADRAO_TEMPORADA_ROTULO.search(rotulo)
    m_tam = _PADRAO_TAMANHO.search(rotulo)
    tamanho_gb = None
    if m_tam:
        valor = float(m_tam.group(1).replace(",", "."))
        tamanho_gb = valor if m_tam.group(2).upper() == "GB" else valor / 1024
    return {
        "rotulo": rotulo,
        "episodio": episodios[0] if episodios else None,
        "episodios": episodios,
        "temporada": int(m_temp.group(1)) if m_temp else None,
        "resolucao": _resolucao(rotulo),
        "audio": _nivel_audio(rotulo),
        "cam": bool(_PADRAO_CAM.search(rotulo)),
        "tamanho_gb": tamanho_gb,
    }


def hash_do_magnet(magnet):
    """Hash do magnet em hexadecimal minúsculo, o formato que o qBittorrent
    devolve. Aceita também o hash em base32 (32 caracteres), que o site usa
    em parte dos magnets (caso real VisionQuest, 2026-10-17: o MOIRAI dava
    "todas as opções da página já travaram ou não abriram")."""
    # ":" opcional: magnet quebrado pelo site vem como "urnbtih<hash>" (ver _reparar_magnet).
    m = re.search(r"btih:?([0-9a-fA-F]{40}|[A-Za-z2-7]{32})(?![A-Za-z0-9])", magnet or "", re.IGNORECASE)
    if not m:
        return None
    valor = m.group(1)
    if len(valor) == 40:
        return valor.lower()
    return base64.b32decode(valor.upper()).hex()


# Trackers públicos para o magnet reconstruído; o qBittorrent também acha
# pares pelo DHT.
_TRACKERS_PUBLICOS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
)


def _reparar_magnet(magnet):
    """Parte dos botões é um magnet direto que o site publica sem ":" e "/"
    ("magnet:?xt=urnbtih3ed5...&tr=udptracker.opentrackr.org1337announce",
    caso real A Casa do Dragão S03E02, 2026-10-17). O hash continua inteiro,
    então o magnet é refeito a partir dele, com trackers públicos."""
    if "urn:btih:" in magnet.lower():
        return magnet
    hash_torrent = hash_do_magnet(magnet)
    if not hash_torrent:
        return None
    return f"magnet:?xt=urn:btih:{hash_torrent}" + "".join(f"&tr={t}" for t in _TRACKERS_PUBLICOS)


def chave_qualidade(opcao):
    """Maior = melhor: áudio, depois resolução, depois tamanho (mesma
    resolução e áudio, o arquivo maior costuma ter mais bitrate)."""
    return (opcao["audio"], opcao["resolucao"], opcao.get("tamanho_gb") or 0)


def interpretar_pagina(html, url):
    """Título, capa, trailer e opções de download da página de um título."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1.titulo") or soup.find("meta", property="og:title")
    titulo_bruto = (h1.get("content") if h1 and h1.name == "meta" else h1.get_text(strip=True) if h1 else "") or ""
    capa = soup.select_one("img.attachment-large[src]")  # pôster do título (não há og:image)
    trailer = soup.select_one("div.movie-trailer iframe[src]")
    if trailer and "youtube" not in trailer["src"]:
        trailer = None  # páginas antigas trazem lixo ("http://Tulsa%20King")
    opcoes = []
    for botao in soup.select("a.download-btn[href]"):
        rotulo_tag = botao.select_one(".download-btn-label")
        rotulo = (rotulo_tag or botao).get_text(" ", strip=True)
        opcao = interpretar_rotulo(rotulo)
        opcao["link"] = urljoin(url, botao["href"])
        opcoes.append(opcao)
    return {
        "titulo": _PADRAO_SUFIXO_TITULO.sub("", titulo_bruto).strip(),
        "capa_url": capa["src"] if capa else None,
        "trailer_url": trailer["src"] if trailer else None,
        "opcoes": opcoes,
    }


_PADRAO_MAGNET = re.compile(r"magnet:\?xt=urn:btih:[^\"'<>\s]+", re.IGNORECASE)


def resolver_magnet(link, url_pagina):
    """`/link.php?id=...` devolve uma página com o magnet no HTML (sem
    redirecionamento nem desafio). None se não achar."""
    if link.startswith("magnet:"):
        return _reparar_magnet(link)
    try:
        resp = requests.get(link, headers={"User-Agent": at.USER_AGENT, "Referer": url_pagina}, timeout=at._TIMEOUT_REQUEST)
        resp.raise_for_status()
    except Exception as e:
        print(f" [SISTEMA] Erro ao abrir o link de download do TorrentFilmesHD: {e}")
        return None
    m = _PADRAO_MAGNET.search(resp.text)
    return html_lib.unescape(m.group(0)) if m else None


# ======================================================
# 🗂️ NOMES E PASTAS
# ======================================================
def titulo_base_serie(titulo):
    """"Slow Horses 6ª Temporada (2026)" -> "Slow Horses"."""
    sem_ano = re.sub(r"\s*\(\d{4}\)\s*$", "", titulo)
    return re.sub(r"\s*\d+ª\s*Temporada\s*$", "", sem_ano, flags=re.IGNORECASE).strip() or sem_ano


def temporada_do_registro(registro):
    m = _PADRAO_TEMPORADA_TITULO.search(registro.get("titulo", ""))
    return int(m.group(1)) if m else (registro.get("temporada") or 1)


def pasta_destino(registro):
    """Série: `<pasta de séries>\\<Série>`, todas as temporadas juntas (o
    SxxEyy no nome separa); filme: a raiz da pasta de filmes. Sem pasta por
    temporada desde 2026-10-17, pedido do usuário: "acho desnecessario ter
    esse tanto de pasta, so quero o video"."""
    if registro.get("tipo") == "serie":
        nome = at._sanitizar_nome_arquivo(titulo_base_serie(registro["titulo"]))
        return os.path.normpath(os.path.join(obter_midia_pasta_series(), nome))
    return os.path.normpath(obter_midia_pasta_filmes())


def _parte_episodios(episodios):
    """[1] -> "E01"; [1, 2] -> "E01-E02"."""
    if len(episodios) > 1:
        return f"E{episodios[0]:02d}-E{episodios[-1]:02d}"
    return f"E{episodios[0]:02d}"


def nome_final(registro, ident, extensao, episodios=None):
    if registro.get("tipo") == "serie":
        base = at._sanitizar_nome_arquivo(titulo_base_serie(registro["titulo"]))
        parte = _parte_episodios(episodios or episodios_do_ident(ident))
        return f"{base} - S{temporada_do_registro(registro):02d}{parte}{extensao}"
    return f"{at._sanitizar_nome_arquivo(registro['titulo'])}{extensao}"


# ======================================================
# ⬇️ DOWNLOADS
# ======================================================
def _status(registro, ident):
    return registro.get("episodios", {}).get(str(ident))


# "assistido" (apagado da pasta ou visto pelo ▶️, 2026-10-18) também já foi
# baixado: nunca baixa de novo.
_STATUS_JA_TEM = ("baixado", "assistido")


def _ja_tem(registro, ident):
    return _status(registro, ident) in _STATUS_JA_TEM


def episodios_do_ident(ident):
    """"3" -> [3]; "1-2" (um torrent com dois episódios) -> [1, 2];
    filme e pacote -> []."""
    partes = str(ident).split("-")
    return [int(p) for p in partes] if all(p.isdigit() for p in partes) else []


def _ident_de_episodios(episodios):
    return "-".join(str(n) for n in episodios)


def _idents_a_baixar(registro, opcoes):
    """{ident: [opções]} do que ainda falta baixar. Série com botão por
    episódio baixa cada episódio; sem nenhum, cai no pacote da temporada.
    Filme junta todas as opções sob IDENT_FILME. CAM nunca entra."""
    boas = [o for o in opcoes if not o["cam"]]
    em_andamento = registro.get("downloads_em_andamento", {})
    if registro.get("tipo") != "serie":
        if boas and not _ja_tem(registro, IDENT_FILME) and IDENT_FILME not in em_andamento:
            return {IDENT_FILME: boas}
        return {}
    por_ident = {}
    for opcao in boas:
        if opcao["episodios"]:
            por_ident.setdefault(_ident_de_episodios(opcao["episodios"]), []).append(opcao)
    if not por_ident:
        pacotes = [o for o in boas if not o["episodios"]]
        if pacotes and not _ja_tem(registro, IDENT_PACOTE) and IDENT_PACOTE not in em_andamento:
            return {IDENT_PACOTE: pacotes}
        return {}
    if IDENT_PACOTE in em_andamento or _ja_tem(registro, IDENT_PACOTE):
        return {}
    # Já baixado ou baixando, inclusive dentro de um torrent de vários episódios.
    cobertos = {int(n) for n, s in registro.get("episodios", {}).items()
                if n.isdigit() and s in _STATUS_JA_TEM + ("conflito_qbittorrent",)}
    for ident in em_andamento:
        cobertos.update(episodios_do_ident(ident))
    com_opcao_propria = {episodios_do_ident(i)[0] for i in por_ident if len(episodios_do_ident(i)) == 1}
    resultado = {}
    for ident, lista in por_ident.items():
        faltando = [n for n in episodios_do_ident(ident) if n not in cobertos]
        if not faltando:
            continue
        if len(episodios_do_ident(ident)) > 1 and all(n in com_opcao_propria for n in faltando):
            continue  # cada episódio que falta tem botão próprio: baixa um por um
        resultado[ident] = lista
    return resultado


def _rotulo_ident(registro, ident):
    if ident == IDENT_FILME:
        return registro["titulo"]
    if ident == IDENT_PACOTE:
        return f"{registro['titulo']} (temporada completa)"
    return f"{titulo_base_serie(registro['titulo'])} S{temporada_do_registro(registro):02d}{_parte_episodios(episodios_do_ident(ident))}"


def _proxima_opcao(registro, ident, opcoes, url_pagina):
    """Melhor opção ainda não tentada cujo link vira magnet: (opção, magnet,
    hash) ou None. Só resolve o link das opções que chegam a ser avaliadas."""
    tentados = set(registro.get("magnets_tentados", {}).get(str(ident), []))
    for opcao in sorted(opcoes, key=chave_qualidade, reverse=True):
        magnet = resolver_magnet(opcao["link"], url_pagina)
        hash_torrent = hash_do_magnet(magnet)
        if hash_torrent and hash_torrent not in tentados:
            return opcao, magnet, hash_torrent
    return None


def _disparar(cliente, registro, ident, escolha):
    """Manda a opção escolhida (_proxima_opcao) pro qBittorrent. Devolve
    (rótulo da opção, motivo da falha)."""
    import qbittorrentapi
    if escolha is None:
        return None, "todas as opções da página já travaram ou não abriram"
    opcao, magnet, hash_torrent = escolha
    pasta = pasta_destino(registro)
    os.makedirs(pasta, exist_ok=True)
    try:
        cliente.torrents_add(urls=magnet, save_path=pasta, category=CATEGORIA_QBITTORRENT)
    except qbittorrentapi.Conflict409Error:
        for marcado in [str(ident)] + [str(n) for n in episodios_do_ident(ident)]:
            registro.setdefault("episodios", {})[marcado] = "conflito_qbittorrent"
        return None, "o torrent já estava no qBittorrent (baixado por fora)"
    except Exception as e:
        return None, f"erro no qBittorrent: {e}"
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    registro.setdefault("downloads_em_andamento", {})[str(ident)] = {
        "hash": hash_torrent, "pasta": pasta, "opcao": opcao["rotulo"],
        "progresso": 0.0, "progresso_em": agora,
    }
    registro.setdefault("download_disparado_em", {})[str(ident)] = f"{agora} ({hash_torrent})"
    for limpo in [str(ident)] + [str(n) for n in episodios_do_ident(ident)]:
        registro.get("falhas_download", {}).pop(limpo, None)
    return opcao["rotulo"], None


def _atualizar_da_pagina(registro, pagina):
    registro["titulo"] = pagina["titulo"] or registro.get("titulo")
    registro["capa_url"] = registro.get("capa_url") or pagina["capa_url"]
    registro["trailer_url"] = pagina["trailer_url"] or registro.get("trailer_url")
    registro["consultado_em"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    episodios = sorted({n for o in pagina["opcoes"] for n in o["episodios"]})
    registro["episodios_no_site"] = episodios
    boas = [o for o in pagina["opcoes"] if not o["cam"]]
    registro["so_cam"] = bool(pagina["opcoes"]) and not boas
    if boas:
        melhor = max(boas, key=chave_qualidade)
        registro["melhor_versao"] = melhor["rotulo"]


def _baixar_pendentes(cliente, chave, registro):
    """Lê a página e dispara o que falta. Devolve ([rótulos disparados],
    [(rótulo, motivo)] das falhas). Não salva - quem chama salva."""
    try:
        pagina = interpretar_pagina(_obter_html(registro["url"]), registro["url"])
    except Exception as e:
        return [], [(registro["titulo"], f"página não abriu: {e}")]
    _atualizar_da_pagina(registro, pagina)
    disparados, falhas = [], []
    a_baixar = _idents_a_baixar(registro, pagina["opcoes"])
    # Falha de algo que já saiu da fila (baixou, baixado por fora, outra
    # chave depois da correção de 0.3.1) não vale mais: some do card.
    registro["falhas_download"] = {k: v for k, v in (registro.get("falhas_download") or {}).items() if k in a_baixar}
    for ident, opcoes in sorted(a_baixar.items(), key=lambda par: (episodios_do_ident(par[0]) or [0])[0]):
        escolhida, motivo = _disparar(cliente, registro, ident, _proxima_opcao(registro, ident, opcoes, registro["url"]))
        rotulo = _rotulo_ident(registro, ident)
        if escolhida:
            disparados.append(rotulo)
            print(f" [SISTEMA] 🎬 Baixando {rotulo} ({escolhida})...")
        else:
            registro.setdefault("falhas_download", {})[str(ident)] = {
                "motivo": motivo, "desde": registro.get("falhas_download", {}).get(str(ident), {}).get(
                    "desde", datetime.now().strftime("%Y-%m-%d %H:%M")),
            }
            falhas.append((rotulo, motivo))
    return disparados, falhas


def baixar_pendentes_de(chave):
    """Rota da tela ao marcar 👍 (dispara na hora, sem esperar a checagem).
    Devolve (quantidade disparada, aviso quando nada foi disparado)."""
    with lock_estado_midia:
        titulos = _carregar()
        registro = titulos.get(chave)
        if not registro or registro.get("interesse") != "tenho_interesse":
            return 0, None
        if not at.qbittorrent_configurado():
            return 0, at._AVISO_SEM_QBITTORRENT
        try:
            cliente = at._cliente_qbittorrent()
        except Exception as e:
            return 0, f"Não consegui falar com o qBittorrent: {e}"
        disparados, falhas = _baixar_pendentes(cliente, chave, registro)
        _salvar(titulos)
    if disparados:
        return len(disparados), None
    if falhas:
        return 0, "\n".join(f"- {rotulo}: {motivo}" for rotulo, motivo in falhas)
    if registro.get("so_cam"):
        return 0, "Por enquanto a página só tem gravação de cinema (CAM/TS). O MOIRAI baixa quando sair uma versão de verdade."
    return 0, "Nada novo pra baixar agora."


def adicionar_por_link(url):
    """Título colado à mão (fora da home). Marca 👍 direto, como no anime.
    Devolve (chave, erro)."""
    url = url.strip()
    if not url.startswith(URL_BASE):
        return None, f"O link precisa ser do TorrentFilmesHD ({URL_BASE}/...)."
    try:
        pagina = interpretar_pagina(_obter_html(url), url)
    except Exception as e:
        return None, f"Erro ao acessar a página: {e}"
    if not pagina["titulo"]:
        return None, "Não achei o título na página - a estrutura do site pode ter mudado."
    chave = _chave_de_url(url)
    with lock_estado_midia:
        titulos = _carregar()
        registro = titulos.get(chave) or {"episodios": {}, "downloads_em_andamento": {}}
        registro.update({"tipo": tipo_do_slug(chave), "url": url, "interesse": "tenho_interesse"})
        _atualizar_da_pagina(registro, pagina)
        registro.setdefault("visto_em", datetime.now().strftime("%Y-%m-%d %H:%M"))
        titulos[chave] = registro
        _salvar(titulos)
    return chave, None


# ======================================================
# 🔄 CHECAGEM (junto da checagem dos animes)
# ======================================================
def verificar_lancamentos():
    """Home -> títulos novos viram "pendente"; cada título acompanhado tem a
    página consultada e baixa o que faltar. Devolve dict com `novos`
    [(titulo, tipo)], `disparados` [rótulo], `falhas` [(rótulo, motivo)] e
    `itens_home`."""
    resultado = {"novos": [], "disparados": [], "falhas": [], "itens_home": 0}
    itens = listar_lancamentos_home()
    resultado["itens_home"] = len(itens)
    with lock_estado_midia:
        titulos = _carregar()
        agora = datetime.now().strftime("%Y-%m-%d %H:%M")
        for item in itens:
            registro = titulos.get(item["chave"])
            if registro is None:
                registro = titulos[item["chave"]] = {
                    "tipo": item["tipo"], "titulo": item["titulo"], "url": item["url"],
                    "capa_url": item["capa_url"], "interesse": "pendente", "visto_em": agora,
                    "episodios": {}, "downloads_em_andamento": {},
                }
                resultado["novos"].append((item["titulo"], item["tipo"]))
            elif registro.get("interesse") != "sem_interesse":
                registro["titulo"] = item["titulo"]
                registro["capa_url"] = item["capa_url"] or registro.get("capa_url")
        acompanhados = [(c, r) for c, r in titulos.items() if r.get("interesse") == "tenho_interesse"
                        and not (r.get("tipo") == "filme" and _ja_tem(r, IDENT_FILME))]
        cliente = None
        if acompanhados and at.qbittorrent_configurado():
            try:
                cliente = at._cliente_qbittorrent()
            except Exception as e:
                resultado["falhas"].append(("qBittorrent", str(e)))
        if cliente is not None:
            for chave, registro in acompanhados:
                disparados, falhas = _baixar_pendentes(cliente, chave, registro)
                resultado["disparados"] += disparados
                resultado["falhas"] += falhas
        _salvar(titulos)
    return resultado


_NOME_TIPO = {"serie": "série", "filme": "filme"}


_LIMITE_LINHAS_NOVOS = 15  # a 1ª checagem traz a home inteira (~50 títulos)


def formatar_texto_novos(novos):
    if not novos:
        return None
    cabecalho = "📺 Séries e filmes novos no TorrentFilmesHD (marque interesse no Painel, aba Série/Filme)"
    if len(novos) > _LIMITE_LINHAS_NOVOS:
        series = sum(1 for _, tipo in novos if tipo == "serie")
        return f"{cabecalho}: {series} séries e {len(novos) - series} filmes entraram em Pendentes."
    linhas = [f"- {titulo} ({_NOME_TIPO.get(tipo, tipo)})" for titulo, tipo in novos]
    return f"{cabecalho}:\n" + "\n".join(linhas)


def formatar_texto_disparados(disparados):
    if not disparados:
        return None
    return "⬇ Começou a baixar:\n" + "\n".join(f"- {rotulo}" for rotulo in disparados)


# ======================================================
# ⏱️ DOWNLOADS EM ANDAMENTO (loop de ~30s do main.py)
# ======================================================
_PADRAO_SXXEXX_ARQUIVO = re.compile(r"S\d{1,2}E(\d{1,3})(?:-?E?(\d{1,3}))?(?!\d)", re.IGNORECASE)


def _concluir(cliente, registro, ident, info, torrent):
    """Renomeia o(s) vídeo(s) para o nome final na pasta de destino. True se
    deu certo; False mantém o download em acompanhamento (tenta de novo)."""
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    if ident == IDENT_PACOTE or len(episodios_do_ident(ident)) > 1:
        # Vários episódios: cada vídeo é renomeado pelo SxxEyy do próprio nome
        # (um arquivo "S01E01-02" com os dois juntos vira "S01E01-E02").
        renomeados = 0
        for arquivo in cliente.torrents_files(torrent_hash=info["hash"]):
            caminho = os.path.join(torrent.save_path, arquivo.name)
            m = _PADRAO_SXXEXX_ARQUIVO.search(os.path.basename(arquivo.name))
            if not m or not caminho.lower().endswith(at.EXTENSOES_VIDEO) or not os.path.exists(caminho):
                continue
            inicio = int(m.group(1))
            episodios = list(range(inicio, max(inicio, int(m.group(2) or inicio)) + 1))
            destino = os.path.join(torrent.save_path, nome_final(registro, ident, os.path.splitext(caminho)[1], episodios))
            if not os.path.exists(destino):
                at._renomear_episodio_avulso(cliente, torrent, caminho, destino)
                at._remover_subpastas_vazias_do_lote(torrent.save_path, caminho)
            for n in episodios:
                registro.setdefault("episodios", {})[str(n)] = "baixado"
                registro.setdefault("arquivos", {})[str(n)] = destino
            renomeados += 1
        if not renomeados:
            registro.setdefault("erro_renomear", {})[ident] = f"{agora}: nenhum vídeo com SxxEyy no nome"
        return True
    arquivo_video = at._maior_arquivo_video(torrent.content_path or info["pasta"])
    if not arquivo_video:
        registro.setdefault("erro_renomear", {})[ident] = f"{agora}: nenhum vídeo em '{torrent.content_path}'"
        return False
    destino = os.path.join(torrent.save_path, nome_final(registro, ident, os.path.splitext(arquivo_video)[1]))
    if os.path.normcase(destino) != os.path.normcase(arquivo_video):
        if os.path.exists(destino):
            registro.setdefault("erro_renomear", {})[ident] = f"{agora}: destino já existia ({os.path.basename(destino)})"
            return False
        at._renomear_episodio_avulso(cliente, torrent, arquivo_video, destino)
        at._remover_subpastas_vazias_do_lote(torrent.save_path, arquivo_video)
    for marcado in episodios_do_ident(ident) or [ident]:
        registro.setdefault("arquivos", {})[str(marcado)] = destino
    return True


def _apagar_sobras(torrent):
    """Apaga a pasta que o torrent criou dentro da pasta de destino depois que
    o vídeo saiu dela (2026-10-17, pedido do usuário: "so quero o video... o
    resto pode ser excluido"). O que sobra é propaganda do release (`.url`,
    `.png`, `.nfo`). Nunca apaga se ainda houver algum vídeo dentro, nem nada
    fora da pasta de destino."""
    pasta = torrent.content_path
    if not pasta or not os.path.isdir(pasta) or not at._dentro_da_pasta(pasta, torrent.save_path):
        return
    for _raiz, _pastas, arquivos in os.walk(pasta):
        if any(nome.lower().endswith(at.EXTENSOES_VIDEO) for nome in arquivos):
            return
    try:
        shutil.rmtree(pasta)
    except OSError as e:
        print(f" [SISTEMA] Não consegui apagar as sobras em {pasta}: {e}")


def verificar_downloads():
    """Concluído -> renomeia, marca "baixado" e tira da lista do
    qBittorrent (`delete_files=False`, nunca apaga arquivo). Sem progresso
    por `anime_download_travado_horas` (2h sem nenhum seeder) -> troca pela
    próxima opção da página, se houver; sem outra opção, o torrent fica."""
    with lock_estado_midia:
        titulos = _carregar()
        pendentes = [(c, ident, info) for c, r in titulos.items() for ident, info in r.get("downloads_em_andamento", {}).items()]
        if not pendentes or not at.qbittorrent_configurado():
            return 0
        try:
            cliente = at._cliente_qbittorrent()
        except Exception as e:
            print(f" [SISTEMA] Erro ao conectar no qBittorrent pra checar séries/filmes: {e}")
            return 0
        agora = datetime.now()
        limite = timedelta(hours=obter_anime_download_travado_horas())
        limite_sem_seed = min(limite, timedelta(hours=_HORAS_TRAVADO_SEM_SEED))
        concluidos, travados = 0, []
        for chave, ident, info in pendentes:
            registro = titulos[chave]
            try:
                torrents = cliente.torrents_info(torrent_hashes=info["hash"])
            except Exception as e:
                print(f" [SISTEMA] Erro ao consultar torrent {info['hash']}: {e}")
                continue
            torrent = torrents[0] if torrents else None
            destino = pasta_destino(registro)
            if torrent is not None and os.path.normcase(os.path.normpath(torrent.save_path)) != os.path.normcase(destino):
                # Pasta mudou (config ou regra nova): o qBittorrent move os
                # arquivos; a conclusão espera a próxima volta.
                try:
                    cliente.torrents_set_location(location=destino, torrent_hashes=info["hash"])
                    info["pasta"] = destino
                except Exception as e:
                    print(f" [SISTEMA] Erro ao mover {_rotulo_ident(registro, ident)} para {destino}: {e}")
                continue
            if torrent is None or torrent.progress < 1.0:
                if torrent is not None and torrent.progress > info.get("progresso", 0.0):
                    info["progresso"] = torrent.progress
                    info["progresso_em"] = agora.strftime("%Y-%m-%d %H:%M")
                elif agora - datetime.strptime(info["progresso_em"], "%Y-%m-%d %H:%M") >= (
                        limite_sem_seed if torrent is None or getattr(torrent, "num_seeds", None) == 0 else limite):
                    travados.append((chave, ident, info))
                continue
            try:
                if not _concluir(cliente, registro, ident, info, torrent):
                    continue
            except Exception as e:
                registro.setdefault("erro_renomear", {})[ident] = f"{agora:%Y-%m-%d %H:%M}: {e}"
                print(f" [SISTEMA] Erro ao renomear {_rotulo_ident(registro, ident)}: {e}")
                continue
            for marcado in [ident] + [str(n) for n in episodios_do_ident(ident)]:
                registro.setdefault("episodios", {})[marcado] = "baixado"
            registro.setdefault("baixado_em", {})[ident] = agora.strftime("%Y-%m-%d %H:%M")
            registro["downloads_em_andamento"].pop(ident, None)
            registro.get("falhas_download", {}).pop(ident, None)
            concluidos += 1
            print(f" [SISTEMA] 🎬 Download concluído: {_rotulo_ident(registro, ident)}.")
            try:
                cliente.torrents_delete(delete_files=False, torrent_hashes=info["hash"])
            except Exception as e:
                print(f" [SISTEMA] Erro ao tirar {_rotulo_ident(registro, ident)} da lista do qBittorrent: {e}")
            _apagar_sobras(torrent)
        for chave, ident, info in travados:
            _tratar_travado(cliente, chave, titulos[chave], ident, info)
        _salvar(titulos)
        return concluidos


def _tratar_travado(cliente, chave, registro, ident, info):
    try:
        pagina = interpretar_pagina(_obter_html(registro["url"]), registro["url"])
    except Exception:
        return  # site fora: tenta na próxima volta
    tentados = registro.setdefault("magnets_tentados", {}).setdefault(ident, [])
    if info["hash"] not in tentados:
        tentados.append(info["hash"])
    # Mesmas opções do ident, ignorando o status (ele está "em andamento").
    candidatas = _idents_a_baixar({**registro, "downloads_em_andamento": {}, "episodios": {}}, pagina["opcoes"]).get(ident, [])
    escolha = _proxima_opcao(registro, ident, candidatas, registro["url"])
    rotulo = _rotulo_ident(registro, ident)
    if escolha is None:
        # Sem outra opção, tirar o torrent só piora: um seeder pode aparecer.
        info["progresso_em"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        if not info.get("sem_alternativa"):
            info["sem_alternativa"] = True
            print(f" [SISTEMA] ⚠️ {rotulo}: download travado e sem outra opção na página - mantido no qBittorrent.")
        return
    registro["downloads_em_andamento"].pop(ident, None)
    try:
        cliente.torrents_delete(delete_files=False, torrent_hashes=info["hash"])
    except Exception as e:
        print(f" [SISTEMA] Erro ao remover torrent travado {info['hash']}: {e}")
    escolhida, motivo = _disparar(cliente, registro, ident, escolha)
    print(f" [SISTEMA] ⚠️ {rotulo}: download travado - " + (f"trocado por {escolhida}." if escolhida else f"sem troca ({motivo})."))


# ======================================================
# 👁 ASSISTIDOS + TRAKT (2026-10-18)
# ======================================================
# Pedido do usuário: "como serie é pesada, eu so removo, n movo para
# assistido" e "mesma regra p filme". Episódio ou filme baixado pelo MOIRAI
# que sumiu da pasta conta como assistido; o ▶️ do card abre, confirma que
# assistiu e apaga sozinho. Cada um vai pro histórico do Trakt.

def _caminho_do(registro, chave_ep):
    """Arquivo final de um episódio ("3") ou do filme ("filme"). Registro
    anterior a 2026-10-18 não guardava o caminho: procura pelo nome final."""
    caminho = registro.get("arquivos", {}).get(chave_ep)
    if caminho:
        return caminho
    pasta = pasta_destino(registro)
    if registro.get("tipo") == "serie":
        prefixo = nome_final(registro, chave_ep, "")
    else:
        prefixo = nome_final(registro, IDENT_FILME, "")
    try:
        for nome in sorted(os.listdir(pasta)):
            if nome.startswith(prefixo) and nome.lower().endswith(at.EXTENSOES_VIDEO):
                caminho = os.path.join(pasta, nome)
                registro.setdefault("arquivos", {})[chave_ep] = caminho
                return caminho
    except OSError:
        pass
    return None


def _marcar_assistido(registro, chave_ep, quando=None):
    registro.setdefault("episodios", {})[chave_ep] = "assistido"
    registro.setdefault("assistido_em", {})[chave_ep] = quando or datetime.now().strftime("%Y-%m-%d %H:%M")
    pendentes = registro.setdefault("trakt_pendentes", [])
    if chave_ep not in pendentes:
        pendentes.append(chave_ep)


def _chaves_baixadas(registro):
    """Episódios (números) ou "filme" com status "baixado"."""
    return [c for c, st in registro.get("episodios", {}).items()
            if st == "baixado" and (c.isdigit() or c == IDENT_FILME)]


def sincronizar_assistidos():
    """Loop de downloads (~30s): o que estava baixado e sumiu da pasta vira
    "assistido", e o que estiver pendente vai pro Trakt. Só olha o disco; o
    Trakt só é chamado quando há o que marcar. Devolve quantos viraram
    assistidos nesta volta."""
    with lock_estado_midia:
        titulos = _carregar()
        mudou, novos = False, 0
        for registro in titulos.values():
            em_andamento = registro.get("downloads_em_andamento", {})
            cobertos = {str(n) for ident in em_andamento for n in episodios_do_ident(ident)} | set(em_andamento)
            for chave_ep in _chaves_baixadas(registro):
                if chave_ep in cobertos:
                    continue
                antes = dict(registro.get("arquivos", {}))
                caminho = _caminho_do(registro, chave_ep)
                mudou = mudou or registro.get("arquivos", {}) != antes
                if caminho and os.path.exists(caminho):
                    continue
                _marcar_assistido(registro, chave_ep)
                mudou, novos = True, novos + 1
                print(f" [SISTEMA] 👁 {_rotulo_assistido(registro, chave_ep)}: arquivo apagado, marcado como assistido.")
        if any(r.get("trakt_pendentes") for r in titulos.values()):
            mudou = _enviar_ao_trakt(titulos) or mudou
        if mudou:
            _salvar(titulos)
        return novos


def _rotulo_assistido(registro, chave_ep):
    return registro["titulo"] if chave_ep == IDENT_FILME else _rotulo_ident(registro, chave_ep)


def _enviar_ao_trakt(titulos):
    """Manda os pendentes de todos os títulos numa chamada só. Título que o
    Trakt não acha fica pendente com `trakt_erro` (aparece no card). True se
    algo mudou no estado."""
    from moirai.integrations.trakt import trakt_client
    if not trakt_client.esta_conectado():
        return False
    episodios, filmes, enviados, mudou = [], [], [], False
    for registro in titulos.values():
        pendentes = registro.get("trakt_pendentes") or []
        if not pendentes:
            continue
        if not registro.get("trakt_id"):
            tipo = "show" if registro.get("tipo") == "serie" else "movie"
            titulo = titulo_base_serie(registro["titulo"]) if tipo == "show" else re.sub(r"\s*\(\d{4}\)\s*$", "", registro["titulo"])
            ano = re.search(r"\((\d{4})\)", registro["titulo"])
            achado = trakt_client.buscar(tipo, titulo, ano.group(1) if ano else None)
            mudou = True
            if not achado:
                registro["trakt_erro"] = f"não achei \"{titulo}\" no Trakt"
                continue
            registro.update({"trakt_id": achado["trakt"], "trakt_titulo": f"{achado['titulo']} ({achado['ano']})"})
            registro.pop("trakt_erro", None)
        for chave_ep in pendentes:
            quando = registro.get("assistido_em", {}).get(chave_ep)
            if chave_ep == IDENT_FILME:
                filmes.append((registro["trakt_id"], quando))
            else:
                episodios.append((registro["trakt_id"], temporada_do_registro(registro), int(chave_ep), quando))
        enviados.append(registro)
    if not enviados:
        return mudou
    ok, erro = trakt_client.marcar_assistidos(episodios, filmes)
    for registro in enviados:
        if ok:
            registro.setdefault("trakt_enviado", []).extend(registro["trakt_pendentes"])
            registro["trakt_pendentes"] = []
            registro.pop("trakt_erro", None)
        else:
            registro["trakt_erro"] = erro
    print(" [SISTEMA] 👁 Trakt: " + (f"{len(episodios)} episódio(s) e {len(filmes)} filme(s) marcados." if ok else f"não marcou ({erro})."))
    return True


def _proximo_para_assistir(registro):
    """(chave do episódio/filme, caminho) do menor episódio baixado que ainda
    está no disco, ou (None, None)."""
    chaves = sorted(_chaves_baixadas(registro), key=lambda c: int(c) if c.isdigit() else 0)
    for chave_ep in chaves:
        caminho = _caminho_do(registro, chave_ep)
        if caminho and os.path.exists(caminho):
            return chave_ep, caminho
    return None, None


def assistir(chave):
    """▶️ do card: abre o próximo episódio (ou o filme) no player e, quando
    confirma que assistiu (mesma regra do ▶️ dos animes), apaga o arquivo e
    marca como assistido. Devolve (sucesso, rótulo, erro)."""
    with lock_estado_midia:
        titulos = _carregar()
        registro = titulos.get(chave)
        if not registro:
            return False, None, "título não encontrado"
        chave_ep, caminho = _proximo_para_assistir(registro)
        _salvar(titulos)  # _caminho_do pode ter descoberto caminhos
    if not caminho:
        return False, None, "nenhum episódio baixado na pasta"
    rotulo = _rotulo_assistido(registro, chave_ep)
    at.abrir_e_monitorar(caminho, lambda: _concluir_assistido(chave, chave_ep, caminho))
    return True, rotulo, None


def _concluir_assistido(chave, chave_ep, caminho):
    try:
        os.remove(caminho)
    except OSError as e:
        print(f" [SISTEMA] Não consegui apagar {caminho} depois de assistido: {e}")
    with lock_estado_midia:
        titulos = _carregar()
        registro = titulos.get(chave)
        if registro and _status(registro, chave_ep) != "assistido":
            _marcar_assistido(registro, chave_ep)
            print(f" [SISTEMA] 👁 {_rotulo_assistido(registro, chave_ep)}: assistido pelo ▶️.")
            _enviar_ao_trakt(titulos)
            _salvar(titulos)

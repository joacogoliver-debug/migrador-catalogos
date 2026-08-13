"""
Descarga de portadas vía iTunes Search API (pública, sin clave).

Lógica portada de `fchavonet/full_stack-itunes_artwork_finder`: la API devuelve
`artworkUrl100` (100x100) y el tamaño se cambia reescribiendo la URL —
`100x100bb.jpg` → `3000x3000bb.jpg`. No es un truco frágil: es el esquema de
nombres del CDN de Apple y es la forma estándar de pedir alta resolución.

Bajamos a 3000x3000 y, si Apple no tiene esa resolución para el release, caemos
a 2000 y después a 1200. La mayoría de las distribuidoras piden portada
cuadrada de 3000x3000 para ingesta, así que arrancamos por ahí.
"""

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
USER_AGENT = "RelevarCatalogo/2.0 (migrador de catalogo)"

# Apple limita ~20 pedidos/min por IP sin clave. Con 4 hilos y reintento
# quedamos holgados sin que nos corte.
PORTADAS_WORKERS = 4
RESOLUCIONES = (3000, 2000, 1200)

# Umbral de similitud título-a-título para aceptar un match. Por debajo de esto
# preferimos no traer portada antes que traer la portada de otro disco.
MIN_RATIO = 0.62


def _norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _strip_ruido(titulo):
    """Saca sufijos que Apple no suele tener en el título del álbum."""
    t = re.sub(
        r"\s*[\(\[]\s*(official|video|audio|lyric[s]?|visualizer|hd|4k|remaster(ed)?|"
        r"en vivo|live|explicit)\b[^\)\]]*[\)\]]",
        "", titulo or "", flags=re.I,
    )
    return re.sub(r"\s+", " ", t).strip(" -–—|")


def _http_json(url, retries=3):
    for intento in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            if intento == retries - 1:
                return None
            time.sleep(1.5 * (intento + 1))
    return None


def _upscale(url, px):
    """Reescribe la URL del CDN de Apple al tamaño pedido."""
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{px}x{px}bb.jpg", url or "")


def buscar_portada(artista, album, upc=""):
    """Busca la portada de un producto. Devuelve dict con url y datos del match,
    o None si no hubo coincidencia confiable.

    Si hay UPC probamos primero por ahí: es un match exacto, sin ambigüedad.
    Recién si eso falla vamos a la búsqueda por texto.
    """
    if upc:
        data = _http_json(f"{ITUNES_LOOKUP}?{urllib.parse.urlencode({'upc': upc})}")
        res = (data or {}).get("results") or []
        if res:
            r = res[0]
            return {
                "url100": r.get("artworkUrl100", ""),
                "matched_album": r.get("collectionName", ""),
                "matched_artist": r.get("artistName", ""),
                "match": "upc",
                "ratio": 1.0,
            }

    album_limpio = _strip_ruido(album)
    termino = f"{artista} {album_limpio}".strip()
    if not termino:
        return None

    params = {"term": termino, "entity": "album", "limit": 25}
    data = _http_json(f"{ITUNES_SEARCH}?{urllib.parse.urlencode(params)}")
    candidatos = (data or {}).get("results") or []
    if not candidatos:
        return None

    obj_album, obj_art = _norm(album_limpio), _norm(artista)
    mejor, mejor_ratio = None, 0.0
    for c in candidatos:
        ratio = SequenceMatcher(None, obj_album, _norm(c.get("collectionName"))).ratio()
        # El artista tiene que coincidir; si no, es otro disco con título parecido.
        art_ok = bool(obj_art) and (
            obj_art in _norm(c.get("artistName")) or _norm(c.get("artistName")) in obj_art
        )
        if art_ok:
            ratio += 0.15
        if ratio > mejor_ratio:
            mejor, mejor_ratio = c, ratio

    if not mejor or mejor_ratio < MIN_RATIO:
        return None

    return {
        "url100": mejor.get("artworkUrl100", ""),
        "matched_album": mejor.get("collectionName", ""),
        "matched_artist": mejor.get("artistName", ""),
        "match": "alta" if mejor_ratio >= 0.85 else "media",
        "ratio": round(min(mejor_ratio, 1.0), 3),
    }


def descargar_portada(url100):
    """Baja la portada en la resolución más alta disponible.
    Devuelve (bytes, px) o (None, 0)."""
    for px in RESOLUCIONES:
        url = _upscale(url100, px)
        if not url:
            return None, 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            # Apple devuelve un JPG chico en lugar de 404 cuando no tiene el
            # tamaño: si pesa muy poco, probamos la resolución siguiente.
            if data and len(data) > 10_000:
                return data, px
        except Exception:
            continue
    return None, 0


def fetch_portadas(productos, artista, log=print):
    """Busca y baja la portada de cada producto, en paralelo.

    No escribe archivos: deja los bytes en `p["cover_bytes"]` para que los
    empaquete el ZIP. Registra el resultado en `p["cover_status"]`.
    """
    def una(p):
        info = buscar_portada(artista, p.get("title", ""), p.get("upc", ""))
        if not info:
            p["cover_bytes"], p["cover_px"] = None, 0
            p["cover_status"] = "sin match en iTunes"
            return p
        data, px = descargar_portada(info["url100"])
        p["cover_bytes"], p["cover_px"] = data, px
        p["cover_match"] = info["match"]
        if data:
            p["cover_status"] = f"ok {px}x{px} (match {info['match']})"
        else:
            p["cover_status"] = "match encontrado pero falló la descarga"
        return p

    with ThreadPoolExecutor(max_workers=PORTADAS_WORKERS) as ex:
        for i, p in enumerate(ex.map(una, productos), 1):
            log(f"[portadas] {i}/{len(productos)} {p['title'][:40]} → {p['cover_status']}")

    ok = sum(1 for p in productos if p.get("cover_bytes"))
    log(f"[portadas] listas {ok}/{len(productos)}")
    return productos

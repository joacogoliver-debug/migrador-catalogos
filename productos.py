"""
Agrupación del catálogo relevado en *productos* (álbum / EP / single) y filtros
de selección para la migración.

El relevamiento de `relevar_core` devuelve una lista plana de tracks (uno por
video de YouTube). Para migrar un catálogo no se entrega track por track: se
entrega **producto por producto**, porque así lo recibe la distribuidora nueva
(un UPC, una portada, un conjunto de audios). Este módulo hace esa traducción.

Limitación conocida y deliberada: YouTube no expone el número de track dentro
del álbum. El orden que armamos acá es una *aproximación* por fecha de subida
(los álbumes suelen subirse en orden). El campo `track_number` queda en None
hasta que lo complete el enriquecimiento por Deezer, y el reporte de migración
avisa cuando un producto quedó sin orden confirmado.
"""

import re
import unicodedata
from collections import Counter

SIN_ALBUM = "(single / sin álbum)"

# Umbrales de formato. Un single puede traer un lado B, y la frontera EP/álbum
# más usada en la industria es 6-7 tracks.
MAX_TRACKS_SINGLE = 2
MAX_TRACKS_EP = 6


def _norm(s):
    """Normaliza para comparar títulos: sin acentos, sin puntuación, minúsculas."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _kind(n_tracks):
    if n_tracks <= MAX_TRACKS_SINGLE:
        return "single"
    if n_tracks <= MAX_TRACKS_EP:
        return "ep"
    return "album"


def _mode(values):
    """Valor no vacío más frecuente (para consolidar sello/distribuidora/UPC
    cuando los tracks de un mismo álbum traen datos despareros)."""
    vals = [v for v in values if v not in (None, "", "(sin datos)")]
    if not vals:
        return ""
    return Counter(vals).most_common(1)[0][0]


def _slug(s, maxlen=60):
    """Nombre seguro para carpeta en Windows/macOS/Linux."""
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r'[<>:"/\\|?*]', "", s)          # prohibidos en Windows
    s = re.sub(r"[\x00-\x1f]", "", s)           # control
    s = re.sub(r"\s+", " ", s).strip(" .")      # Windows no admite terminar en " " ni "."
    return (s[:maxlen].strip() or "Sin titulo")


# ============================================================
# Agrupación
# ============================================================

def group_products(tracks, artist=""):
    """Agrupa una lista de tracks en productos.

    Criterio: los tracks que declaran un álbum real se agrupan por
    (álbum normalizado, año); los que vienen sin álbum quedan como singles
    independientes. Separar por año es intencional: evita fusionar un álbum con
    su reedición, que en una migración son dos productos con UPC distinto.
    """
    grupos = {}
    for t in tracks:
        album = (t.get("album") or "").strip()
        year = t.get("release_year") or ""
        if not album or album == SIN_ALBUM:
            # Single: clave única por video, nunca se fusiona con otro.
            clave = ("__single__", t.get("video_id") or id(t))
        else:
            clave = (_norm(album), str(year))
        grupos.setdefault(clave, []).append(t)

    productos = []
    for clave, ts in grupos.items():
        ts = sorted(ts, key=lambda x: (x.get("upload_date") or "", x.get("track") or ""))
        es_single = clave[0] == "__single__"
        titulo = ts[0].get("track", "") if es_single else (ts[0].get("album") or "").strip()

        años = [t.get("release_year") for t in ts if t.get("release_year")]
        fechas = [t.get("upload_date") for t in ts if t.get("upload_date")]

        for i, t in enumerate(ts, 1):
            # Orden provisorio por fecha de subida; se marca como no confirmado.
            t["track_number"] = t.get("track_number") or i

        productos.append({
            "product_id": f"p{len(productos) + 1:03d}",
            "title": titulo,
            "kind": _kind(len(ts)),
            "artist": artist or "",
            "release_year": min(años) if años else "",
            "release_date": min(fechas) if fechas else "",
            "label": _mode(t.get("label") for t in ts),
            "distributor": _mode(t.get("distributor") for t in ts),
            "category": _mode(t.get("category") for t in ts),
            "upc": _mode(t.get("upc") for t in ts),
            "tracks": ts,
            "track_count": len(ts),
            "total_views": sum(int(t.get("views") or 0) for t in ts),
            # True cuando el orden salió sólo de la fecha de subida (sin confirmar).
            "order_unconfirmed": len(ts) > 1,
        })

    # Más nuevo primero: es el orden en que la gente revisa su catálogo.
    productos.sort(key=lambda p: (str(p["release_year"] or ""), p["release_date"] or ""), reverse=True)
    for i, p in enumerate(productos, 1):
        p["product_id"] = f"p{i:03d}"
        p["folder"] = folder_name(p)
    return productos


def folder_name(p):
    """Nombre de carpeta del producto dentro del ZIP: '2019 - Album [UPC]'."""
    año = p.get("release_year") or "s-f"          # s-f = sin fecha
    base = f"{año} - {_slug(p.get('title'))}"
    upc = (p.get("upc") or "").strip()
    return f"{base} [{upc}]" if upc else base


# ============================================================
# Filtros de selección
# ============================================================

def filter_products(productos, ids=None, year_from=None, year_to=None,
                    date_from=None, date_to=None, distributors=None):
    """Filtra productos para la migración. Los filtros se combinan con AND.

    - ids:          selección manual por product_id (lista o set)
    - year_from/to: rango por año de lanzamiento (℗), inclusive
    - date_from/to: rango por fecha de publicación 'YYYY-MM-DD', inclusive
    - distributors: nombres de distribuidora (match parcial, sin acentos)

    Un producto sin año declarado queda fuera si se filtra por año: preferimos
    excluirlo antes que colarlo en una selección donde no sabemos si entra.
    """
    sel = productos

    if ids is not None:
        ids = set(ids)
        sel = [p for p in sel if p["product_id"] in ids]

    if year_from is not None:
        sel = [p for p in sel if p["release_year"] and int(p["release_year"]) >= int(year_from)]
    if year_to is not None:
        sel = [p for p in sel if p["release_year"] and int(p["release_year"]) <= int(year_to)]

    if date_from is not None:
        sel = [p for p in sel if p["release_date"] and p["release_date"] >= date_from]
    if date_to is not None:
        sel = [p for p in sel if p["release_date"] and p["release_date"] <= date_to]

    if distributors:
        buscados = [_norm(d) for d in distributors if _norm(d)]
        sel = [p for p in sel if any(b in _norm(p["distributor"]) for b in buscados)]

    return sel


def distributor_options(productos):
    """Distribuidoras presentes, con su conteo — para armar el filtro en la UI."""
    c = Counter(p["distributor"] for p in productos if p.get("distributor"))
    return [{"name": n, "count": k} for n, k in c.most_common()]


def year_range(productos):
    """(mín, máx) de años presentes, o (None, None) — para el slider de fechas."""
    años = sorted({int(p["release_year"]) for p in productos if p.get("release_year")})
    return (años[0], años[-1]) if años else (None, None)


def summarize(productos):
    """Resumen de una selección, para mostrar antes de descargar."""
    return {
        "products": len(productos),
        "tracks": sum(p["track_count"] for p in productos),
        "albums": sum(1 for p in productos if p["kind"] == "album"),
        "eps": sum(1 for p in productos if p["kind"] == "ep"),
        "singles": sum(1 for p in productos if p["kind"] == "single"),
        "with_upc": sum(1 for p in productos if p.get("upc")),
        "with_isrc": sum(1 for p in productos for t in p["tracks"] if t.get("isrc")),
        "views": sum(p["total_views"] for p in productos),
    }

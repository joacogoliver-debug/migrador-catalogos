"""
Validación pre-entrega: chequea el catálogo contra los requisitos que usan las
distribuidoras, para saber qué va a ser rechazado ANTES de mandarlo.

Dos niveles:
  error  — la distribuidora lo va a rechazar. Hay que corregirlo.
  aviso  — pasa la ingesta pero conviene revisarlo.

Todo se valida sin red y sin dependencias extra: los códigos se verifican por
sus reglas de formato y dígito verificador, y las dimensiones de las portadas se
leen de las cabeceras del archivo (JPEG/PNG) sin necesidad de Pillow.
"""

import re
import unicodedata
from datetime import date

# ISRC: CC-XXX-YY-NNNNN (12 caracteres sin guiones).
#   CC     país (2 letras; incluye códigos especiales como QM/QZ que usan varios
#          registrantes digitales)
#   XXX    registrante (3 alfanuméricos)
#   YY     año de referencia (2 dígitos)
#   NNNNN  designación (5 dígitos)
RE_ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")

# Mínimos de portada. 1400x1400 es el piso de Spotify/Apple; 3000x3000 es lo
# recomendado y lo que piden varias distribuidoras para ingesta.
COVER_MIN = 1400
COVER_RECOMENDADO = 3000

# Residuos típicos de títulos de YouTube que no van en una ficha de release.
RE_RUIDO_TITULO = re.compile(
    r"\b(official\s*(music\s*)?video|video\s*oficial|lyric\s*video|video\s*lyric|"
    r"letra\s*oficial|audio\s*oficial|official\s*audio|visualizer|"
    r"hd|4k|full\s*album|en\s*vivo|live\s*session)\b", re.I)

DURACION_MAX_SOSPECHOSA = 15 * 60   # 15 min: puede ser un mix o un álbum entero
ANIO_MIN = 1900


def _hallazgo(nivel, codigo, mensaje, producto="", track=None):
    return {"nivel": nivel, "codigo": codigo, "mensaje": mensaje,
            "producto": producto, "track": track}


# ============================================================
# Códigos
# ============================================================

def isrc_valido(isrc):
    """True si el ISRC tiene formato válido. Acepta guiones y minúsculas."""
    if not isrc:
        return False
    limpio = re.sub(r"[\s\-]", "", str(isrc)).upper()
    return bool(RE_ISRC.fullmatch(limpio))


def _gtin_check_digit(digitos_sin_check):
    """Dígito verificador GTIN (sirve para UPC-A y EAN-13).
    Pesos 3 y 1 alternados desde la derecha."""
    total = 0
    for i, ch in enumerate(reversed(digitos_sin_check)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def upc_valido(upc):
    """Valida largo y dígito verificador de un UPC-A (12) o EAN-13 (13).
    Devuelve (ok, motivo)."""
    if not upc:
        return False, "vacío"
    limpio = re.sub(r"[\s\-]", "", str(upc))
    if not limpio.isdigit():
        return False, "tiene caracteres que no son dígitos"
    if len(limpio) not in (12, 13):
        return False, f"tiene {len(limpio)} dígitos (se esperan 12 para UPC-A o 13 para EAN-13)"
    esperado = _gtin_check_digit(limpio[:-1])
    if int(limpio[-1]) != esperado:
        return False, f"dígito verificador incorrecto (termina en {limpio[-1]}, debería ser {esperado})"
    return True, ""


# ============================================================
# Portadas — dimensiones y espacio de color desde la cabecera
# ============================================================

def medir_imagen(data):
    """Lee (ancho, alto, componentes) de un JPEG o PNG desde sus bytes.

    `componentes` sólo aplica a JPEG: 3 = YCbCr (lo normal), 4 = CMYK (las
    distribuidoras lo rechazan), 1 = escala de grises. Devuelve None si no
    reconoce el formato.
    """
    # Cada formato valida su propio mínimo: PNG necesita 24 bytes para llegar al
    # IHDR, JPEG bastante menos. Un mínimo único para los dos descartaría
    # archivos válidos.
    if not data or len(data) < 4:
        return None

    # PNG: firma de 8 bytes, luego el chunk IHDR con ancho y alto.
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) < 24:
            return None
        ancho = int.from_bytes(data[16:20], "big")
        alto = int.from_bytes(data[20:24], "big")
        return ancho, alto, 3

    # JPEG: recorremos los marcadores hasta encontrar un SOF.
    if data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i < n - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marcador = data[i + 1]
            # SOF0..SOF15 traen las dimensiones; C4/C8/CC no son SOF.
            if 0xC0 <= marcador <= 0xCF and marcador not in (0xC4, 0xC8, 0xCC):
                alto = int.from_bytes(data[i + 5:i + 7], "big")
                ancho = int.from_bytes(data[i + 7:i + 9], "big")
                comps = data[i + 9]
                return ancho, alto, comps
            if marcador in (0xD8, 0x01) or 0xD0 <= marcador <= 0xD7:
                i += 2
                continue
            largo = int.from_bytes(data[i + 2:i + 4], "big")
            if largo <= 0:
                break
            i += 2 + largo
    return None


def validar_portada(p):
    """Valida la portada de un producto. Devuelve lista de hallazgos."""
    out = []
    nombre = p.get("title", "")
    data = p.get("cover_bytes")

    if not data:
        out.append(_hallazgo("aviso", "portada_falta",
                             f"sin portada ({p.get('cover_status', 'no buscada')})", nombre))
        return out

    medida = medir_imagen(data)
    if not medida:
        out.append(_hallazgo("error", "portada_ilegible",
                             "no pude leer las dimensiones de la portada "
                             "(¿formato no soportado?)", nombre))
        return out

    ancho, alto, comps = medida

    if ancho != alto:
        out.append(_hallazgo("error", "portada_no_cuadrada",
                             f"la portada es {ancho}x{alto} y tiene que ser cuadrada", nombre))
    if min(ancho, alto) < COVER_MIN:
        out.append(_hallazgo("error", "portada_chica",
                             f"la portada es {ancho}x{alto}, por debajo del mínimo "
                             f"de {COVER_MIN}x{COVER_MIN}", nombre))
    elif min(ancho, alto) < COVER_RECOMENDADO:
        out.append(_hallazgo("aviso", "portada_bajo_recomendado",
                             f"la portada es {ancho}x{alto}: entra, pero el recomendado "
                             f"es {COVER_RECOMENDADO}x{COVER_RECOMENDADO}", nombre))
    if comps == 4:
        out.append(_hallazgo("error", "portada_cmyk",
                             "la portada parece estar en CMYK y tiene que ser RGB", nombre))
    return out


# ============================================================
# Validación del catálogo
# ============================================================

def validar(productos, artista=""):
    """Valida una selección de productos. Devuelve dict con hallazgos y resumen."""
    out = []
    anio_actual = date.today().year

    # --- por producto ---
    for p in productos:
        nombre = p.get("title", "") or "(sin título)"

        if not (p.get("title") or "").strip():
            out.append(_hallazgo("error", "producto_sin_titulo",
                                 "el producto no tiene título", nombre))

        upc = (p.get("upc") or "").strip()
        if not upc:
            out.append(_hallazgo("aviso", "upc_falta",
                                 "sin UPC: la distribuidora va a asignar uno nuevo "
                                 "(se pierde la continuidad del release)", nombre))
        else:
            ok, motivo = upc_valido(upc)
            if not ok:
                out.append(_hallazgo("error", "upc_invalido",
                                     f"UPC '{upc}' inválido: {motivo}", nombre))

        anio = p.get("release_year")
        if not anio:
            out.append(_hallazgo("aviso", "anio_falta",
                                 "sin año de lanzamiento", nombre))
        else:
            try:
                a = int(anio)
                if a > anio_actual:
                    out.append(_hallazgo("error", "anio_futuro",
                                         f"el año de lanzamiento ({a}) está en el futuro", nombre))
                elif a < ANIO_MIN:
                    out.append(_hallazgo("error", "anio_absurdo",
                                         f"el año de lanzamiento ({a}) no es plausible", nombre))
            except (TypeError, ValueError):
                out.append(_hallazgo("error", "anio_invalido",
                                     f"el año de lanzamiento ('{anio}') no es un número", nombre))

        if not (p.get("label") or "").strip():
            out.append(_hallazgo("aviso", "sello_falta",
                                 "sin sello (℗): varias distribuidoras lo piden", nombre))

        if p.get("order_unconfirmed"):
            out.append(_hallazgo("aviso", "orden_sin_confirmar",
                                 "el orden de los tracks es estimado por fecha de subida, "
                                 "no confirmado", nombre))

        out.extend(validar_portada(p))

        # --- por track ---
        for t in p.get("tracks", []):
            titulo = t.get("track", "") or "(sin título)"

            if not (t.get("track") or "").strip():
                out.append(_hallazgo("error", "track_sin_titulo",
                                     "el track no tiene título", nombre, titulo))

            isrc = (t.get("isrc") or "").strip()
            if not isrc:
                out.append(_hallazgo("aviso", "isrc_falta",
                                     "sin ISRC: la distribuidora va a asignar uno nuevo "
                                     "(se pierde el historial de la grabación)", nombre, titulo))
            elif not isrc_valido(isrc):
                out.append(_hallazgo("error", "isrc_invalido",
                                     f"ISRC '{isrc}' no tiene formato válido "
                                     "(se esperan 12 caracteres: CC-XXX-YY-NNNNN)",
                                     nombre, titulo))

            dur = int(t.get("duration_s") or 0)
            if dur <= 0:
                out.append(_hallazgo("error", "duracion_falta",
                                     "sin duración", nombre, titulo))
            elif dur > DURACION_MAX_SOSPECHOSA:
                out.append(_hallazgo("aviso", "duracion_larga",
                                     f"dura {dur // 60} min: puede ser un mix o un álbum "
                                     "entero en un solo video, no un track",
                                     nombre, titulo))

            if RE_RUIDO_TITULO.search(titulo):
                out.append(_hallazgo("aviso", "titulo_con_ruido",
                                     "el título arrastra texto de YouTube "
                                     "(ej. 'Official Video'): conviene limpiarlo",
                                     nombre, titulo))

    out.extend(_duplicados(productos))

    errores = [h for h in out if h["nivel"] == "error"]
    avisos = [h for h in out if h["nivel"] == "aviso"]
    return {
        "hallazgos": out,
        "errores": errores,
        "avisos": avisos,
        "apto": not errores,
        "resumen": {
            "productos": len(productos),
            "tracks": sum(len(p.get("tracks", [])) for p in productos),
            "errores": len(errores),
            "avisos": len(avisos),
        },
    }


def _duplicados(productos):
    """ISRC repetido entre tracks y UPC repetido entre productos.

    Un código duplicado es error: identifica de forma única una grabación o un
    release, así que repetirlo hace que la distribuidora rechace la ingesta o,
    peor, que sobrescriba el release equivocado.
    """
    out = []

    vistos_isrc = {}
    for p in productos:
        for t in p.get("tracks", []):
            isrc = re.sub(r"[\s\-]", "", (t.get("isrc") or "")).upper()
            if not isrc:
                continue
            donde = f"{p.get('title', '')} / {t.get('track', '')}"
            if isrc in vistos_isrc:
                out.append(_hallazgo(
                    "error", "isrc_duplicado",
                    f"el ISRC {isrc} está repetido: aparece en '{vistos_isrc[isrc]}' "
                    f"y en '{donde}'", p.get("title", ""), t.get("track", "")))
            else:
                vistos_isrc[isrc] = donde

    vistos_upc = {}
    for p in productos:
        upc = re.sub(r"[\s\-]", "", (p.get("upc") or ""))
        if not upc:
            continue
        if upc in vistos_upc:
            out.append(_hallazgo(
                "error", "upc_duplicado",
                f"el UPC {upc} está repetido: lo usan '{vistos_upc[upc]}' y "
                f"'{p.get('title', '')}'", p.get("title", "")))
        else:
            vistos_upc[upc] = p.get("title", "")

    return out


# ============================================================
# Reporte
# ============================================================

def _sin_acentos(s):
    return unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode("ascii")


def reporte_validacion(res, artista=""):
    """Reporte de texto de la validación, para incluir en el ZIP."""
    L = [f"VALIDACIÓN PRE-ENTREGA — {artista}", f"Generado: {date.today().isoformat()}",
         "=" * 68, ""]
    r = res["resumen"]
    L.append(f"Productos revisados : {r['productos']}")
    L.append(f"Tracks revisados    : {r['tracks']}")
    L.append(f"Errores             : {r['errores']}")
    L.append(f"Avisos              : {r['avisos']}")
    L.append("")

    if res["apto"]:
        L.append("Sin errores: el catálogo no tiene problemas que causen rechazo.")
    else:
        L.append("! Hay errores que las distribuidoras suelen rechazar. Corregirlos")
        L.append("  antes de la entrega.")
    L.append("")

    for nivel, titulo in (("error", "ERRORES"), ("aviso", "AVISOS")):
        grupo = [h for h in res["hallazgos"] if h["nivel"] == nivel]
        if not grupo:
            continue
        L.append(titulo)
        L.append("-" * 68)
        por_producto = {}
        for h in grupo:
            por_producto.setdefault(h["producto"] or "(catálogo)", []).append(h)
        for prod, hs in por_producto.items():
            L.append(f"  {prod}")
            for h in hs:
                donde = f" [{h['track']}]" if h["track"] else ""
                L.append(f"      - {h['mensaje']}{donde}")
        L.append("")

    return "\n".join(L) + "\n"

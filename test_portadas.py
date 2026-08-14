# -*- coding: utf-8 -*-
"""Test offline del módulo de portadas (sin red, sin pytest).

Corré:  python test_portadas.py
Sale 0 si todo pasa, 1 si algo falla. No necesita claves ni internet.

Lo importante que cubre: que el estado de la portada reporte la resolución
REAL y no la pedida. Apple sirve el tamaño máximo que tiene y responde 200
aunque sea más chico que el pedido — pedir 3000x3000 puede devolver 600x604.
Reportar el tamaño pedido haría que la planilla diga que la portada cumple el
mínimo de ingesta cuando en realidad la van a rechazar.
"""
import os
import sys
import struct
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(nombre):
    path = os.path.join(HERE, f"{nombre}.py")
    spec = importlib.util.spec_from_file_location(nombre, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = mod
    spec.loader.exec_module(mod)
    return mod


def _jpeg(ancho, alto, comps=3):
    sof = (b"\xff\xc0" + struct.pack(">H", 8 + 3 * comps) + b"\x08"
           + struct.pack(">HH", alto, ancho) + bytes([comps]) + b"\x00" * (3 * comps))
    return b"\xff\xd8" + sof + b"\xff\xd9" + b"\x00" * 200


def main():
    _load("validar")                 # portadas importa medir_imagen de validar
    PT = _load("portadas")
    fails = []

    def expect(name, got, want):
        if got != want:
            fails.append(f"  [{name}] got {got!r}, want {want!r}")

    def check(name, cond, detalle=""):
        if not cond:
            fails.append(f"  [{name}] falló {detalle}")

    # --- Reescritura de la URL del CDN de Apple --------------------------
    base = "https://is1-ssl.mzstatic.com/image/thumb/abc/100x100bb.jpg"
    expect("upscale.3000", PT._upscale(base, 3000),
           "https://is1-ssl.mzstatic.com/image/thumb/abc/3000x3000bb.jpg")
    expect("upscale.desde_otro_tamano", PT._upscale(base.replace("100x100", "500x500"), 2000),
           "https://is1-ssl.mzstatic.com/image/thumb/abc/2000x2000bb.jpg")
    expect("upscale.png", PT._upscale(base.replace(".jpg", ".png"), 1200),
           "https://is1-ssl.mzstatic.com/image/thumb/abc/1200x1200bb.jpg")
    expect("upscale.vacio", PT._upscale("", 3000), "")

    # --- Limpieza de títulos --------------------------------------------
    expect("ruido.official_video", PT._strip_ruido("Tema (Official Video)"), "Tema")
    expect("ruido.remaster", PT._strip_ruido("Album [Remastered 2011]"), "Album")
    expect("ruido.en_vivo", PT._strip_ruido("Disco (En Vivo)"), "Disco")
    expect("ruido.limpio_queda_igual", PT._strip_ruido("Bocanada"), "Bocanada")
    # No debe comerse paréntesis que son parte del título.
    expect("ruido.conserva_parentesis_util",
           PT._strip_ruido("Cosquillas (feat. Alguien)"), "Cosquillas (feat. Alguien)")

    # --- El estado reporta la resolución REAL ---------------------------
    # Reemplazamos la búsqueda y la descarga para no tocar la red.
    PT.buscar_portada = lambda artista, album, upc="": {
        "url100": "https://x/100x100bb.jpg", "matched_album": album,
        "matched_artist": artista, "match": "alta", "ratio": 1.0}

    def con_resolucion(px_real, no_cuadrada=False):
        alto = px_real if not no_cuadrada else px_real + 4
        PT.descargar_portada = lambda url100, _px=px_real, _a=alto: (
            _jpeg(_px, _a), min(_px, _a))
        p = {"title": "Disco", "upc": ""}
        PT.fetch_portadas([p], "Artista", log=lambda *_: None)
        return p

    # Caso real de Radiohead: Apple sí tiene 3000.
    p = con_resolucion(3000)
    check("estado.3000_es_ok", p["cover_status"].startswith("ok 3000x3000"), p["cover_status"])
    expect("estado.3000_px", p["cover_px"], 3000)

    # Caso real de Daft Punk: pedimos 3000, Apple tiene 1500. Entra en ingesta
    # pero el estado tiene que decir 1500, no 3000.
    p = con_resolucion(1500)
    check("estado.1500_dice_1500", "1500x1500" in p["cover_status"], p["cover_status"])
    check("estado.1500_no_miente_3000", "3000" not in p["cover_status"], p["cover_status"])

    # Caso real de Cerati: 600x604. Debajo del mínimo Y no cuadrada.
    p = con_resolucion(600, no_cuadrada=True)
    check("estado.600_avisa_minimo", "DEBAJO DEL MINIMO" in p["cover_status"], p["cover_status"])
    check("estado.600_no_miente", "3000" not in p["cover_status"], p["cover_status"])

    # Exactamente el mínimo de ingesta: no debe avisar.
    p = con_resolucion(PT.COVER_MIN_INGESTA)
    check("estado.minimo_exacto_no_avisa",
          "DEBAJO DEL MINIMO" not in p["cover_status"], p["cover_status"])

    # Descarga fallida.
    PT.descargar_portada = lambda url100: (None, 0)
    p = {"title": "Disco", "upc": ""}
    PT.fetch_portadas([p], "Artista", log=lambda *_: None)
    check("estado.fallo_descarga", "falló la descarga" in p["cover_status"], p["cover_status"])
    expect("estado.fallo_sin_bytes", p["cover_bytes"], None)

    # Sin match en iTunes.
    PT.buscar_portada = lambda artista, album, upc="": None
    p = {"title": "Disco Inexistente", "upc": ""}
    PT.fetch_portadas([p], "Artista", log=lambda *_: None)
    check("estado.sin_match", "sin match" in p["cover_status"], p["cover_status"])
    expect("estado.sin_match_sin_bytes", p["cover_bytes"], None)

    if fails:
        print("FALLARON:")
        print("\n".join(fails))
        return 1
    print("OK - portadas (resolucion real)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

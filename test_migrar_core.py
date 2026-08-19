# -*- coding: utf-8 -*-
"""Test offline del orquestador (sin red, sin pytest, sin clave).

Corré:  python test_migrar_core.py
Sale 0 si todo pasa, 1 si algo falla.

Existe por un bug concreto: relevar_catalogo() desempaquetaba como tupla el
DICT que devuelve relevar_core.relevar(), y tiraba "too many values to unpack"
en cuanto se relevaba de verdad. Ningún test lo agarró porque todos sembraban
los productos directamente, así que la única función que no se podía probar sin
clave era justo la puerta de entrada de la app.

La idea acá es fijar el CONTRATO entre relevar_core y migrar_core: se reemplaza
relevar_core.relevar por un doble que devuelve exactamente la forma real
(mismas claves), sin tocar la red.
"""
import os
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    import relevar_core
    import migrar_core as M

    fails = []

    def expect(name, got, want):
        if got != want:
            fails.append(f"  [{name}] got {got!r}, want {want!r}")

    def check(name, cond, detalle=""):
        if not cond:
            fails.append(f"  [{name}] falló {detalle}")

    # --- La forma REAL que devuelve relevar_core.relevar() -----------------
    # Si algún día cambia, este test se rompe y avisa: es justamente el punto.
    CLAVES_REALES = {"artist", "channel_title", "tracks", "distribs",
                     "total_views", "units", "codes"}

    import inspect
    fuente = inspect.getsource(relevar_core.relevar)
    for clave in CLAVES_REALES:
        check(f"contrato.devuelve:{clave}", f'"{clave}"' in fuente,
              "relevar() ya no devuelve esta clave; actualizá migrar_core")

    def track(titulo, album, anio, isrc="", upc="", vid="v1"):
        return {"video_id": vid, "track": titulo, "album": album,
                "distributor": "ONErpm", "category": "diy", "label": "Sello",
                "release_year": anio, "isrc": isrc, "upc": upc, "match": "",
                "duration_s": 200, "views": 100, "likes": 1, "comments": 0,
                "upload_date": f"{anio}-01-01", "desc3": "",
                "url": f"https://youtu.be/{vid}"}

    tracks_falsos = [
        track("Tema A", "Disco", 2020, "ARABC2000001", "036000291452", "a1"),
        track("Tema B", "Disco", 2020, "ARABC2000002", "036000291452", "a2"),
        track("Single", "(single / sin álbum)", 2021, "ARABC2100001", "", "b1"),
    ]

    llamadas = {}

    def relevar_doble(url, yt_key, with_codes=True, progress=None, use_musicbrainz=False):
        llamadas["url"] = url
        llamadas["yt_key"] = yt_key
        llamadas["with_codes"] = with_codes
        if progress:
            progress("probando el callback", 0.5)
        # EXACTAMENTE la forma real, incluidas todas las claves.
        return {
            "artist": "Artista Doble",
            "channel_title": "Artista Doble - Topic",
            "tracks": tracks_falsos,
            "distribs": {"ONErpm": {"videos": 3, "views": 300}},
            "total_views": 300,
            "units": 3,
            "codes": {"isrc": 3, "upc": 2, "matched": 3, "source": "Deezer"},
        }

    original = relevar_core.relevar
    relevar_core.relevar = relevar_doble
    try:
        avances = []
        prods, artista, tracks = M.relevar_catalogo(
            "https://www.youtube.com/@Test", "clave-falsa",
            progress=lambda m, f=None: avances.append(m))

        # Lo que se rompía: el desempaquetado.
        expect("relevar_catalogo.artista", artista, "Artista Doble")
        expect("relevar_catalogo.n_tracks", len(tracks), 3)
        check("relevar_catalogo.tracks_son_dicts",
              all(isinstance(t, dict) for t in tracks),
              "si desempaqueta mal, acá vendrían strings (las claves del dict)")
        expect("relevar_catalogo.n_productos", len(prods), 2)   # Disco + single
        check("relevar_catalogo.productos_agrupados",
              {p["title"] for p in prods} == {"Disco", "Single"},
              f"{[p['title'] for p in prods]}")
        check("relevar_catalogo.artista_propagado",
              all(p["artist"] == "Artista Doble" for p in prods))

        # Los argumentos llegan tal cual.
        expect("relevar_catalogo.pasa_url", llamadas["url"], "https://www.youtube.com/@Test")
        expect("relevar_catalogo.pasa_clave", llamadas["yt_key"], "clave-falsa")
        expect("relevar_catalogo.pasa_with_codes", llamadas["with_codes"], True)
        check("relevar_catalogo.progress_llega", "probando el callback" in avances,
              f"avances={avances}")
        check("relevar_catalogo.loguea_productos",
              any("productos" in a for a in avances), f"avances={avances}")

        # --- opciones_de_filtro sobre lo relevado ------------------------
        op = M.opciones_de_filtro(prods)
        expect("opciones.total", op["total"], 2)
        expect("opciones.anio_min", op["año_min"], 2020)
        expect("opciones.anio_max", op["año_max"], 2021)
        check("opciones.distribuidoras", op["distribuidoras"][0]["name"] == "ONErpm")

        # --- flujo completo sin red: sólo planilla ----------------------
        import tempfile
        destino = os.path.join(tempfile.mkdtemp(), "salida.zip")
        res = M.migrar("https://www.youtube.com/@Test", "clave-falsa",
                       quiere_planilla=True, quiere_portadas=False,
                       quiere_audio=False, out_path=destino,
                       log=lambda *_: None)
        expect("migrar.artista", res["artista"], "Artista Doble")
        expect("migrar.productos", res["productos"], 2)
        check("migrar.zip_existe", os.path.exists(res["zip"]))
        check("migrar.pesa", res["bytes"] > 0)
        expect("migrar.resumen_tracks", res["resumen"]["tracks"], 3)

        # Filtro que no deja nada -> error mostrable, no una excepción rara.
        try:
            M.migrar("https://www.youtube.com/@Test", "clave-falsa",
                     year_from=2099, out_path=destino, log=lambda *_: None)
            fails.append("  [migrar.seleccion_vacia] debería lanzar RelevarError")
        except relevar_core.RelevarError as e:
            check("migrar.seleccion_vacia_mensaje", "filtros" in str(e), str(e))
    finally:
        relevar_core.relevar = original

    if fails:
        print("FALLARON:")
        print("\n".join(fails))
        return 1
    print("OK - orquestador (contrato con relevar_core)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

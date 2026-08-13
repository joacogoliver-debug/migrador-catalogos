# -*- coding: utf-8 -*-
"""Test offline de la agrupación en productos y los filtros (sin red, sin pytest).

Corré:  python test_productos.py
Sale 0 si todo pasa, 1 si algo falla. No necesita claves ni internet.

Cubre las reglas que sostienen la selección de la migración:
  - tracks del mismo álbum se agrupan en un producto
  - un álbum y su reedición (mismo título, otro año) NO se fusionan
  - los tracks sin álbum quedan como singles independientes
  - clasificación single / EP / álbum por cantidad de tracks
  - consolidación de sello / distribuidora / UPC despareros
  - filtros por id, por año, por fecha y por distribuidora
  - nombres de carpeta seguros en Windows
"""
import os
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(nombre):
    path = os.path.join(HERE, f"{nombre}.py")
    spec = importlib.util.spec_from_file_location(f"{nombre}_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _t(track, album="", year="", dist="DistroKid", label="Sello", upc="", date="2020-01-01", vid=None):
    """Arma un track con la forma que devuelve relevar_core.build_tracks."""
    return {
        "video_id": vid or f"v{abs(hash((track, album, year))) % 100000}",
        "track": track, "album": album or "(single / sin álbum)",
        "distributor": dist, "category": "diy", "label": label,
        "release_year": year, "isrc": "", "upc": upc, "match": "",
        "duration_s": 180, "views": 100, "likes": 1, "comments": 0,
        "upload_date": date, "desc3": "", "url": "",
    }


def main():
    pr = _load("productos")
    fails = []

    def expect(name, got, want):
        if got != want:
            fails.append(f"  [{name}] got {got!r}, want {want!r}")

    # --- Agrupación por álbum ---------------------------------------------
    tracks = [
        _t("Tema A", "Mi Album", 2019, date="2019-05-01"),
        _t("Tema B", "Mi Album", 2019, date="2019-05-02"),
        _t("Tema C", "Mi Album", 2019, date="2019-05-03"),
    ]
    ps = pr.group_products(tracks, artist="Artista")
    expect("album.n_productos", len(ps), 1)
    expect("album.track_count", ps[0]["track_count"], 3)
    expect("album.kind", ps[0]["kind"], "ep")          # 3 tracks -> EP
    expect("album.title", ps[0]["title"], "Mi Album")
    expect("album.year", ps[0]["release_year"], 2019)
    # Orden provisorio por fecha de subida, marcado como no confirmado.
    expect("album.orden_no_confirmado", ps[0]["order_unconfirmed"], True)
    expect("album.track1", ps[0]["tracks"][0]["track"], "Tema A")

    # --- Álbum vs reedición: mismo título, año distinto -> 2 productos ----
    ps = pr.group_products([
        _t("Tema A", "Clasico", 2005, date="2005-01-01"),
        _t("Tema A", "Clasico", 2020, date="2020-01-01"),
    ])
    expect("reedicion.n_productos", len(ps), 2)

    # Insensible a acentos/puntuación al agrupar el mismo álbum.
    ps = pr.group_products([
        _t("T1", "Corazón Roto", 2018, date="2018-01-01"),
        _t("T2", "Corazon Roto!", 2018, date="2018-01-02"),
    ])
    expect("acentos.n_productos", len(ps), 1)

    # --- Singles: cada uno es su propio producto -------------------------
    ps = pr.group_products([
        _t("Single Uno", "", 2021, vid="a1", date="2021-01-01"),
        _t("Single Dos", "", 2021, vid="a2", date="2021-02-01"),
    ])
    expect("singles.n_productos", len(ps), 2)
    expect("singles.kind", ps[0]["kind"], "single")
    expect("singles.title_es_el_track", sorted(p["title"] for p in ps),
           ["Single Dos", "Single Uno"])

    # --- Clasificación por cantidad de tracks -----------------------------
    def kind_de(n):
        return pr.group_products(
            [_t(f"T{i}", "Disco", 2020, date=f"2020-01-{i:02d}") for i in range(1, n + 1)]
        )[0]["kind"]

    expect("kind.1", kind_de(1), "single")
    expect("kind.2", kind_de(2), "single")
    expect("kind.3", kind_de(3), "ep")
    expect("kind.6", kind_de(6), "ep")
    expect("kind.7", kind_de(7), "album")

    # --- Consolidación de datos despareros -------------------------------
    # Dos tracks traen UPC y sello; uno viene vacío. Gana el valor no vacío.
    ps = pr.group_products([
        _t("T1", "Disco", 2020, upc="123", label="Sello Real", date="2020-01-01"),
        _t("T2", "Disco", 2020, upc="123", label="Sello Real", date="2020-01-02"),
        _t("T3", "Disco", 2020, upc="", label="", date="2020-01-03"),
    ])
    expect("consolida.upc", ps[0]["upc"], "123")
    expect("consolida.label", ps[0]["label"], "Sello Real")

    # "(sin datos)" no debe ganar como distribuidora si hay una real.
    ps = pr.group_products([
        _t("T1", "Disco", 2020, dist="(sin datos)", date="2020-01-01"),
        _t("T2", "Disco", 2020, dist="ONErpm", date="2020-01-02"),
    ])
    expect("consolida.dist", ps[0]["distributor"], "ONErpm")

    # --- Filtros ----------------------------------------------------------
    catalogo = pr.group_products([
        _t("A", "Viejo", 2010, dist="DistroKid", date="2010-06-01"),
        _t("B", "Medio", 2015, dist="ONErpm", date="2015-06-01"),
        _t("C", "Nuevo", 2022, dist="ONErpm", date="2022-06-01"),
    ])
    expect("catalogo.n", len(catalogo), 3)
    # Orden: más nuevo primero.
    expect("catalogo.orden", [p["title"] for p in catalogo], ["Nuevo", "Medio", "Viejo"])

    f = pr.filter_products(catalogo, year_from=2015)
    expect("filtro.year_from", sorted(p["title"] for p in f), ["Medio", "Nuevo"])

    f = pr.filter_products(catalogo, year_to=2015)
    expect("filtro.year_to", sorted(p["title"] for p in f), ["Medio", "Viejo"])

    f = pr.filter_products(catalogo, year_from=2015, year_to=2015)
    expect("filtro.year_rango", [p["title"] for p in f], ["Medio"])

    f = pr.filter_products(catalogo, distributors=["onerpm"])
    expect("filtro.dist_case_insensitive", sorted(p["title"] for p in f), ["Medio", "Nuevo"])

    f = pr.filter_products(catalogo, date_from="2015-01-01", date_to="2015-12-31")
    expect("filtro.fecha", [p["title"] for p in f], ["Medio"])

    ids = [catalogo[0]["product_id"]]
    f = pr.filter_products(catalogo, ids=ids)
    expect("filtro.ids", [p["title"] for p in f], ["Nuevo"])

    # Los filtros se combinan con AND.
    f = pr.filter_products(catalogo, year_from=2015, distributors=["distrokid"])
    expect("filtro.and", f, [])

    # Un producto sin año queda fuera de un filtro por año (no se cuela).
    sin_año = pr.group_products([_t("X", "SinAnio", "", date="2020-01-01")])
    expect("filtro.sin_anio", pr.filter_products(sin_año, year_from=2000), [])

    # --- Opciones para la UI ---------------------------------------------
    expect("opciones.dist", pr.distributor_options(catalogo),
           [{"name": "ONErpm", "count": 2}, {"name": "DistroKid", "count": 1}])
    expect("opciones.years", pr.year_range(catalogo), (2010, 2022))
    expect("opciones.years_vacio", pr.year_range([]), (None, None))

    s = pr.summarize(catalogo)
    expect("resumen.products", s["products"], 3)
    expect("resumen.tracks", s["tracks"], 3)
    expect("resumen.singles", s["singles"], 3)

    # --- Nombres de carpeta seguros --------------------------------------
    expect("carpeta.simple",
           pr.folder_name({"release_year": 2019, "title": "Mi Album", "upc": "123"}),
           "2019 - Mi Album [123]")
    expect("carpeta.sin_upc",
           pr.folder_name({"release_year": 2019, "title": "Mi Album", "upc": ""}),
           "2019 - Mi Album")
    expect("carpeta.sin_fecha",
           pr.folder_name({"release_year": "", "title": "Album", "upc": ""}),
           "s-f - Album")
    # Caracteres prohibidos en Windows: se sacan, no se escapan.
    expect("carpeta.prohibidos",
           pr.folder_name({"release_year": 2020, "title": 'A/B:C*D?"E<F>G|H', "upc": ""}),
           "2020 - ABCDEFGH")
    # Windows no admite carpetas que terminan en punto o espacio.
    expect("carpeta.punto_final",
           pr.folder_name({"release_year": 2020, "title": "Album...", "upc": ""}),
           "2020 - Album")

    # --- Catálogo vacío ---------------------------------------------------
    expect("vacio.group", pr.group_products([]), [])
    expect("vacio.resumen", pr.summarize([])["products"], 0)

    if fails:
        print("FALLARON:")
        print("\n".join(fails))
        return 1
    print("OK — agrupación en productos y filtros")
    return 0


if __name__ == "__main__":
    sys.exit(main())

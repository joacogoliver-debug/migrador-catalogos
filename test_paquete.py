# -*- coding: utf-8 -*-
"""Test offline del empaquetado del entregable (sin red, sin pytest).

Corré:  python test_paquete.py
Sale 0 si todo pasa, 1 si algo falla. No necesita claves ni internet.

Cubre lo que sostiene la confianza en el entregable:
  - la estructura del ZIP es una carpeta por producto
  - las planillas y el reporte están en la raíz
  - los audios lossy quedan marcados [REFERENCIA-LOSSY] en el nombre
  - los audios lossless NO llevan esa marca
  - el reporte lista los pendientes reales (sin UPC, sin portada, sin audio)
  - el reporte avisa fuerte cuando no hubo cuenta de Tidal
  - se respetan los checkboxes (no incluir audio / portadas / planilla)
"""
import os
import sys
import shutil
import tempfile
import zipfile
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(nombre):
    path = os.path.join(HERE, f"{nombre}.py")
    spec = importlib.util.spec_from_file_location(nombre, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = mod          # para que `from audio import ...` resuelva
    spec.loader.exec_module(mod)
    return mod


def main():
    _load("audio")                      # paquete.py importa de audio
    pq = _load("paquete")
    pr = _load("productos")
    fails = []

    def expect(name, got, want):
        if got != want:
            fails.append(f"  [{name}] got {got!r}, want {want!r}")

    def check(name, cond, detalle=""):
        if not cond:
            fails.append(f"  [{name}] falló {detalle}")

    tmp = tempfile.mkdtemp(prefix="test_paquete_")
    try:
        # Audios falsos en disco: uno "lossless", uno "lossy".
        flac = os.path.join(tmp, "a.flac")
        m4a = os.path.join(tmp, "b.m4a")
        with open(flac, "wb") as f:
            f.write(b"FLAC-falso" * 100)
        with open(m4a, "wb") as f:
            f.write(b"AAC-falso" * 100)

        productos = [
            {
                "product_id": "p001", "title": "Album Bueno", "kind": "album",
                "release_year": 2020, "upc": "111", "label": "Sello",
                "distributor": "ONErpm", "category": "diy", "track_count": 1,
                "total_views": 10, "order_unconfirmed": False,
                "folder": "2020 - Album Bueno [111]",
                "cover_bytes": b"\xff\xd8jpeg-falso", "cover_status": "ok 3000x3000",
                "tracks": [{
                    "track": "Tema Lossless", "track_number": 1, "isrc": "ARABC2000001",
                    "duration_s": 200, "views": 10, "url": "https://youtu.be/x",
                    "video_id": "x", "audio_path": flac, "audio_format": ".flac",
                    "audio_label": pq.ETIQUETA_LOSSLESS,
                }],
            },
            {
                "product_id": "p002", "title": "Single Flojo", "kind": "single",
                "release_year": 2021, "upc": "", "label": "", "distributor": "DistroKid",
                "category": "diy", "track_count": 2, "total_views": 5,
                "order_unconfirmed": True, "folder": "2021 - Single Flojo",
                "cover_bytes": None, "cover_status": "sin match en iTunes",
                "tracks": [
                    {"track": "Tema Lossy", "track_number": 1, "isrc": "", "duration_s": 180,
                     "views": 5, "url": "", "video_id": "y", "audio_path": m4a,
                     "audio_format": ".m4a", "audio_label": "lossy"},
                    {"track": "Tema Sin Audio", "track_number": 2, "isrc": "", "duration_s": 90,
                     "views": 0, "url": "", "video_id": "z", "audio_path": None,
                     "audio_format": None, "audio_label": None},
                ],
            },
        ]

        # --- ZIP completo -------------------------------------------------
        zip_path = os.path.join(tmp, "salida.zip")
        ruta, tam = pq.build_zip(productos, "Artista Test", zip_path,
                                 con_tidal=True, log=lambda *_: None)
        check("zip.existe", os.path.exists(ruta))
        check("zip.pesa", tam > 0, f"tam={tam}")

        with zipfile.ZipFile(ruta) as z:
            nombres = z.namelist()
            raiz = nombres[0].split("/")[0]

            # Archivos de raíz.
            check("raiz.leeme", f"{raiz}/_LEEME.txt" in nombres)
            check("raiz.reporte", f"{raiz}/_Reporte de migracion.txt" in nombres)
            check("raiz.maestra", f"{raiz}/_Catalogo completo.xlsx" in nombres)

            # Una carpeta por producto, con su planilla.
            check("prod1.datos", f"{raiz}/2020 - Album Bueno [111]/datos.xlsx" in nombres)
            check("prod2.datos", f"{raiz}/2021 - Single Flojo/datos.xlsx" in nombres)

            # Portada sólo donde había.
            check("prod1.portada", f"{raiz}/2020 - Album Bueno [111]/portada.jpg" in nombres)
            check("prod2.sin_portada",
                  f"{raiz}/2021 - Single Flojo/portada.jpg" not in nombres)

            # El lossless NO lleva marca; el lossy SÍ.
            check("audio.lossless_sin_marca",
                  f"{raiz}/2020 - Album Bueno [111]/01 - Tema Lossless.flac" in nombres,
                  f"nombres={[n for n in nombres if 'Lossless' in n]}")
            check("audio.lossy_marcado",
                  f"{raiz}/2021 - Single Flojo/01 - Tema Lossy [REFERENCIA-LOSSY].m4a" in nombres,
                  f"nombres={[n for n in nombres if 'Lossy' in n]}")
            # El track sin audio no genera archivo.
            check("audio.sin_audio_no_aparece",
                  not any("Tema Sin Audio" in n for n in nombres))

            reporte = z.read(f"{raiz}/_Reporte de migracion.txt").decode("utf-8")

        # --- Contenido del reporte ---------------------------------------
        check("reporte.aptos", "Aptos para entrega (FLAC lossless) : 1" in reporte, reporte[:400])
        check("reporte.referencia", "Sólo referencia (lossy)            : 1" in reporte)
        check("reporte.sin_audio", "Sin audio                          : 1" in reporte)
        check("reporte.pendiente_upc", "sin UPC" in reporte)
        check("reporte.pendiente_portada", "sin portada" in reporte)
        check("reporte.pendiente_orden", "orden de tracks sin confirmar" in reporte)
        check("reporte.pendiente_isrc", "tracks sin ISRC" in reporte)

        # --- Aviso fuerte cuando no hubo Tidal ---------------------------
        rep_sin_tidal = pq.reporte_texto(productos, "Artista Test", con_tidal=False)
        check("reporte.aviso_sin_tidal",
              "NO hay audio apto" in rep_sin_tidal, rep_sin_tidal[:300])
        # Con Tidal conectado pero con lossy, avisa lo otro.
        check("reporte.aviso_sin_master",
              "no tiene máster" in reporte, reporte[:600])

        # --- Respetar los checkboxes -------------------------------------
        z2 = os.path.join(tmp, "solo_planilla.zip")
        pq.build_zip(productos, "Artista Test", z2, incluir_audio=False,
                     incluir_portadas=False, log=lambda *_: None)
        with zipfile.ZipFile(z2) as z:
            n2 = z.namelist()
        check("checkbox.sin_audio", not any(x.endswith((".flac", ".m4a")) for x in n2))
        check("checkbox.sin_portadas", not any(x.endswith("portada.jpg") for x in n2))
        check("checkbox.con_planilla", any(x.endswith("_Catalogo completo.xlsx") for x in n2))

        z3 = os.path.join(tmp, "solo_audio.zip")
        pq.build_zip(productos, "Artista Test", z3, incluir_planilla=False,
                     log=lambda *_: None)
        with zipfile.ZipFile(z3) as z:
            n3 = z.namelist()
        check("checkbox.sin_planilla", not any(x.endswith(".xlsx") for x in n3))
        # El reporte va siempre: es lo que explica qué falta.
        check("checkbox.reporte_siempre", any("Reporte de migracion" in x for x in n3))

        # --- Nombres seguros ---------------------------------------------
        expect("slug.prohibidos", pq._slug_archivo('Tema/Con:Barras*?'), "TemaConBarras")
        expect("slug.punto_final", pq._slug_archivo("Tema..."), "Tema")
        expect("slug.vacio", pq._slug_archivo(""), "sin-titulo")

        # --- Fuente corta -------------------------------------------------
        expect("fuente.lossless",
               pq._fuente_corta({"audio_path": "x", "audio_format": ".flac"}),
               "LOSSLESS (flac)")
        expect("fuente.lossy",
               pq._fuente_corta({"audio_path": "x", "audio_format": ".m4a"}),
               "LOSSY (m4a)")
        expect("fuente.sin", pq._fuente_corta({}), "sin audio")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print("FALLARON:")
        print("\n".join(fails))
        return 1
    print("OK - empaquetado del entregable")
    return 0


if __name__ == "__main__":
    sys.exit(main())

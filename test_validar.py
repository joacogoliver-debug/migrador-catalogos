# -*- coding: utf-8 -*-
"""Test offline del validador pre-entrega (sin red, sin pytest).

Corré:  python test_validar.py
Sale 0 si todo pasa, 1 si algo falla. No necesita claves ni internet.

Cubre las reglas que evitan un rechazo de la distribuidora:
  - formato de ISRC (incluye códigos especiales tipo QM/QZ)
  - dígito verificador de UPC-A y EAN-13, contra códigos reales conocidos
  - códigos duplicados (ISRC entre tracks, UPC entre productos)
  - dimensiones, forma y espacio de color de la portada, leídos de la cabecera
  - campos faltantes y años imposibles
  - separación correcta entre error (rechazo) y aviso (revisar)
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


def _png(ancho, alto):
    """PNG mínimo con IHDR válido (sólo para leer dimensiones)."""
    ihdr = struct.pack(">II", ancho, alto) + b"\x08\x06\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr
            + b"\x00\x00\x00\x00")


def _jpeg(ancho, alto, comps=3):
    """JPEG mínimo con un marcador SOF0 válido."""
    sof = (b"\xff\xc0" + struct.pack(">H", 8 + 3 * comps) + b"\x08"
           + struct.pack(">HH", alto, ancho) + bytes([comps])
           + b"\x00" * (3 * comps))
    return b"\xff\xd8" + sof + b"\xff\xd9"


def _prod(title="Disco", upc="", year=2020, label="Sello", tracks=None,
          cover=None, orden_ok=True, cover_status="ok"):
    return {
        "product_id": "p001", "title": title, "kind": "album", "upc": upc,
        "release_year": year, "label": label, "distributor": "ONErpm",
        "track_count": len(tracks or []), "order_unconfirmed": not orden_ok,
        "cover_bytes": cover, "cover_status": cover_status,
        "tracks": tracks or [],
    }


def _track(track="Tema", isrc="ARABC2000001", dur=200):
    return {"track": track, "isrc": isrc, "duration_s": dur, "track_number": 1}


def main():
    V = _load("validar")
    fails = []

    def expect(name, got, want):
        if got != want:
            fails.append(f"  [{name}] got {got!r}, want {want!r}")

    def codigos(res, nivel=None):
        return sorted(h["codigo"] for h in res["hallazgos"]
                      if nivel is None or h["nivel"] == nivel)

    # --- ISRC ------------------------------------------------------------
    expect("isrc.valido", V.isrc_valido("ARABC2000001"), True)
    expect("isrc.con_guiones", V.isrc_valido("AR-ABC-20-00001"), True)
    expect("isrc.minusculas", V.isrc_valido("arabc2000001"), True)
    # QM/QZ los usan muchos registrantes digitales: tienen que pasar.
    expect("isrc.qm", V.isrc_valido("QM24S2000001"), True)
    expect("isrc.registrante_alfanumerico", V.isrc_valido("USA2P2100001"), True)
    expect("isrc.corto", V.isrc_valido("ARABC200000"), False)
    expect("isrc.largo", V.isrc_valido("ARABC20000012"), False)
    expect("isrc.pais_numerico", V.isrc_valido("12ABC2000001"), False)
    expect("isrc.designacion_con_letra", V.isrc_valido("ARABC200000A"), False)
    expect("isrc.vacio", V.isrc_valido(""), False)
    expect("isrc.none", V.isrc_valido(None), False)

    # --- UPC / EAN: contra códigos reales conocidos ----------------------
    # UPC-A de referencia (Coca-Cola), dígito verificador 2.
    expect("upc.real_ok", V.upc_valido("036000291452")[0], True)
    expect("upc.real_check_mal", V.upc_valido("036000291453")[0], False)
    # EAN-13 de referencia, dígito verificador 3.
    expect("ean.real_ok", V.upc_valido("4006381333931")[0], True)
    expect("ean.real_check_mal", V.upc_valido("4006381333932")[0], False)
    expect("upc.con_guiones", V.upc_valido("0-36000-29145-2")[0], True)
    expect("upc.largo_raro", V.upc_valido("12345")[0], False)
    expect("upc.con_letras", V.upc_valido("03600029145X")[0], False)
    expect("upc.vacio", V.upc_valido("")[0], False)
    # El motivo tiene que ser informativo, no genérico.
    ok, motivo = V.upc_valido("036000291453")
    expect("upc.motivo_menciona_digito", "dígito verificador" in motivo, True)

    # --- Medición de imágenes -------------------------------------------
    expect("img.png", V.medir_imagen(_png(3000, 3000)), (3000, 3000, 3))
    expect("img.jpeg", V.medir_imagen(_jpeg(3000, 3000)), (3000, 3000, 3))
    expect("img.jpeg_cmyk", V.medir_imagen(_jpeg(3000, 3000, 4)), (3000, 3000, 4))
    expect("img.jpeg_rectangular", V.medir_imagen(_jpeg(3000, 1500)), (3000, 1500, 3))
    expect("img.basura", V.medir_imagen(b"no soy una imagen"), None)
    expect("img.vacio", V.medir_imagen(b""), None)

    # --- Portadas --------------------------------------------------------
    def cods_portada(cover, **kw):
        return codigos(V.validar([_prod(cover=cover, tracks=[_track()], **kw)]))

    # 3000x3000 cuadrada RGB: sin hallazgos de portada.
    c = cods_portada(_jpeg(3000, 3000))
    expect("portada.perfecta", [x for x in c if x.startswith("portada")], [])
    expect("portada.chica", "portada_chica" in cods_portada(_jpeg(500, 500)), True)
    expect("portada.no_cuadrada", "portada_no_cuadrada" in cods_portada(_jpeg(3000, 2000)), True)
    expect("portada.cmyk", "portada_cmyk" in cods_portada(_jpeg(3000, 3000, 4)), True)
    expect("portada.bajo_recomendado",
           "portada_bajo_recomendado" in cods_portada(_jpeg(1500, 1500)), True)
    # 1400 es el mínimo: entra (aviso), no es error.
    c = cods_portada(_jpeg(1400, 1400))
    expect("portada.minimo_exacto_no_es_error", "portada_chica" in c, False)
    expect("portada.ilegible", "portada_ilegible" in cods_portada(b"basura!!"), True)
    expect("portada.falta", "portada_falta" in cods_portada(None), True)

    # --- Duplicados ------------------------------------------------------
    res = V.validar([
        _prod(title="A", upc="036000291452", tracks=[_track("T1", "ARABC2000001")]),
        _prod(title="B", upc="036000291452", tracks=[_track("T2", "ARABC2000001")]),
    ])
    expect("dup.isrc", "isrc_duplicado" in codigos(res, "error"), True)
    expect("dup.upc", "upc_duplicado" in codigos(res, "error"), True)
    expect("dup.no_apto", res["apto"], False)
    # El mensaje tiene que decir DÓNDE está el duplicado, para poder arreglarlo.
    msj = [h["mensaje"] for h in res["hallazgos"] if h["codigo"] == "isrc_duplicado"][0]
    expect("dup.mensaje_ubica", "T1" in msj and "T2" in msj, True)

    # Con guiones o minúsculas, sigue siendo el mismo ISRC duplicado.
    res = V.validar([
        _prod(title="A", tracks=[_track("T1", "AR-ABC-20-00001")]),
        _prod(title="B", tracks=[_track("T2", "arabc2000001")]),
    ])
    expect("dup.isrc_normalizado", "isrc_duplicado" in codigos(res, "error"), True)

    # Sin códigos no hay falsos duplicados.
    res = V.validar([
        _prod(title="A", upc="", tracks=[_track("T1", "")]),
        _prod(title="B", upc="", tracks=[_track("T2", "")]),
    ])
    expect("dup.vacios_no_cuentan",
           [c for c in codigos(res) if "duplicado" in c], [])

    # --- Años ------------------------------------------------------------
    from datetime import date as _d
    expect("anio.futuro",
           "anio_futuro" in codigos(V.validar([_prod(year=_d.today().year + 2,
                                                     tracks=[_track()])]), "error"), True)
    expect("anio.absurdo",
           "anio_absurdo" in codigos(V.validar([_prod(year=1500, tracks=[_track()])]), "error"), True)
    expect("anio.falta",
           "anio_falta" in codigos(V.validar([_prod(year="", tracks=[_track()])]), "aviso"), True)
    expect("anio.actual_ok",
           "anio_futuro" in codigos(V.validar([_prod(year=_d.today().year,
                                                     tracks=[_track()])])), False)

    # --- Campos faltantes son aviso, no error ---------------------------
    res = V.validar([_prod(upc="", label="", tracks=[_track(isrc="")])])
    expect("falta.upc_es_aviso", "upc_falta" in codigos(res, "aviso"), True)
    expect("falta.isrc_es_aviso", "isrc_falta" in codigos(res, "aviso"), True)
    expect("falta.sello_es_aviso", "sello_falta" in codigos(res, "aviso"), True)
    # Faltar un código no bloquea la entrega: la distribuidora asigna uno nuevo.
    expect("falta.sigue_apto", res["apto"], True)

    # --- Errores de formato sí bloquean ---------------------------------
    res = V.validar([_prod(upc="99999999999", tracks=[_track(isrc="NOESUNISRC")])])
    expect("error.upc_invalido", "upc_invalido" in codigos(res, "error"), True)
    expect("error.isrc_invalido", "isrc_invalido" in codigos(res, "error"), True)
    expect("error.no_apto", res["apto"], False)

    # --- Duración y títulos ---------------------------------------------
    expect("dur.cero",
           "duracion_falta" in codigos(V.validar([_prod(tracks=[_track(dur=0)])]), "error"), True)
    expect("dur.larga",
           "duracion_larga" in codigos(V.validar([_prod(tracks=[_track(dur=3600)])]), "aviso"), True)
    for ruido in ["Tema (Official Video)", "Tema [Lyric Video]", "Tema - Video Oficial",
                  "Tema (Official Audio)", "Tema 4K"]:
        expect(f"titulo.ruido:{ruido}",
               "titulo_con_ruido" in codigos(V.validar([_prod(tracks=[_track(ruido)])])), True)
    # Un título limpio no dispara el aviso.
    expect("titulo.limpio",
           "titulo_con_ruido" in codigos(V.validar([_prod(tracks=[_track("Amanecer")])])), False)
    # "Live Session" es ruido, pero "Vivo" solo no debería serlo.
    expect("titulo.sin_falso_positivo",
           "titulo_con_ruido" in codigos(V.validar([_prod(tracks=[_track("Vivo")])])), False)

    # --- Orden sin confirmar --------------------------------------------
    expect("orden.sin_confirmar",
           "orden_sin_confirmar" in codigos(
               V.validar([_prod(tracks=[_track()], orden_ok=False)]), "aviso"), True)

    # --- Catálogo limpio: apto y sin errores ----------------------------
    res = V.validar([_prod(upc="036000291452", cover=_jpeg(3000, 3000),
                           tracks=[_track("Amanecer", "ARABC2000001")])], "Artista")
    expect("limpio.apto", res["apto"], True)
    expect("limpio.sin_errores", res["resumen"]["errores"], 0)

    # --- Reporte ---------------------------------------------------------
    rep = V.reporte_validacion(res, "Artista")
    expect("reporte.dice_sin_errores", "Sin errores" in rep, True)
    res_malo = V.validar([_prod(upc="123", tracks=[_track(isrc="MAL")])])
    rep = V.reporte_validacion(res_malo, "Artista")
    expect("reporte.tiene_seccion_errores", "ERRORES" in rep, True)
    expect("reporte.advierte", "suelen rechazar" in rep, True)

    # --- Catálogo vacío --------------------------------------------------
    res = V.validar([])
    expect("vacio.apto", res["apto"], True)
    expect("vacio.sin_hallazgos", res["hallazgos"], [])

    if fails:
        print("FALLARON:")
        print("\n".join(fails))
        return 1
    print("OK - validador pre-entrega")
    return 0


if __name__ == "__main__":
    sys.exit(main())

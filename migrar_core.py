"""
Orquestador del migrador de catálogos.

Une las piezas en el flujo de 4 pasos de la app:

  1. relevar(url)                 → catálogo crudo desde YouTube  (relevar_core)
  2. group_products(tracks)       → catálogo dividido en productos (productos)
     filter_products(...)         → selección manual / por fecha / por distribuidora
  3. preparar(seleccion, opciones)→ baja planilla / audios / portadas
  4. build_zip(...)               → carpeta organizada, lista para entregar

Los pasos 1 y 2 no necesitan ninguna credencial del usuario: funcionan para
cualquiera. El audio en calidad de entrega necesita que el usuario conecte su
propia cuenta de Tidal (ver `audio.TidalSession`); sin eso, la app igual entrega
planilla, portadas y audio de referencia claramente etiquetado.
"""

import os
import shutil
import tempfile

import audio as audio_mod
import paquete
import portadas as portadas_mod
import productos as productos_mod
import relevar_core


# ============================================================
# Paso 1 + 2 — relevar y agrupar
# ============================================================

def relevar_catalogo(url, yt_key, with_codes=True, progress=None, use_musicbrainz=False):
    """Releva el catálogo y lo devuelve ya agrupado en productos.

    Devuelve (productos, artista, tracks_planos).
    """
    log = progress or (lambda *_: None)
    # relevar_core.relevar() devuelve un DICT, no una tupla: desempaquetarlo como
    # tupla toma sus claves y tira "too many values to unpack".
    res = relevar_core.relevar(
        url, yt_key, with_codes=with_codes, progress=progress,
        use_musicbrainz=use_musicbrainz,
    )
    tracks = res["tracks"]
    artista = res["artist"]
    prods = productos_mod.group_products(tracks, artist=artista)
    log(f"[productos] {len(prods)} productos a partir de {len(tracks)} tracks")
    return prods, artista, tracks


def opciones_de_filtro(prods):
    """Todo lo que la UI necesita para armar los filtros del paso 2."""
    desde, hasta = productos_mod.year_range(prods)
    return {
        "distribuidoras": productos_mod.distributor_options(prods),
        "año_min": desde,
        "año_max": hasta,
        "total": len(prods),
    }


# ============================================================
# Paso 3 — preparar el contenido pedido
# ============================================================

def preparar(seleccion, artista, quiere_planilla=True, quiere_audio=False,
             quiere_portadas=True, tidal_session=None, usar_referencia=True,
             calidad="LOSSLESS", log=print):
    """Baja lo que se pidió para los productos seleccionados.

    Muta los productos agregándoles `cover_bytes` y, a cada track, `audio_path`
    + `audio_label`. Devuelve (seleccion, dir_temporal_audio, entorno).
    """
    entorno = audio_mod.verificar_entorno()
    dir_audio = None

    if quiere_portadas:
        portadas_mod.fetch_portadas(seleccion, artista, log=log)

    if quiere_audio:
        con_tidal = bool(tidal_session and tidal_session.conectada)
        if con_tidal:
            if not entorno["puede_flac"]:
                log("[audio] falta ffmpeg: no puedo extraer FLAC. Revisá la instalación.")
            else:
                indice, _ = audio_mod.construir_indice_isrc(tidal_session, artista, log=log)
                audio_mod.matchear_por_isrc(seleccion, indice, log=log)
        else:
            log("[audio] sin cuenta de Tidal conectada: el audio será de referencia (lossy)")

        if not entorno["puede_referencia"] and not con_tidal:
            log("[audio] falta yt-dlp o ffmpeg: no puedo bajar ni la referencia")
        else:
            _, dir_audio = audio_mod.fetch_audio(
                seleccion, session=tidal_session if con_tidal else None,
                usar_referencia=usar_referencia, calidad=calidad, log=log,
            )

    return seleccion, dir_audio, entorno


# ============================================================
# Paso 4 — empaquetar
# ============================================================

def empaquetar(seleccion, artista, out_path=None, entorno=None, con_tidal=False,
               incluir_planilla=True, incluir_audio=True, incluir_portadas=True,
               log=print):
    """Arma el ZIP del entregable. Devuelve (ruta, tamaño_bytes)."""
    if out_path is None:
        out_path = os.path.join(
            tempfile.mkdtemp(prefix="migrador_zip_"),
            f"{relevar_core.slugify(artista)}-migracion.zip",
        )
    return paquete.build_zip(
        seleccion, artista, out_path, entorno=entorno, con_tidal=con_tidal,
        incluir_planilla=incluir_planilla, incluir_audio=incluir_audio,
        incluir_portadas=incluir_portadas, log=log,
    )


def limpiar(dir_audio):
    """Borra los audios temporales. Llamar después de servir el ZIP."""
    if dir_audio:
        shutil.rmtree(dir_audio, ignore_errors=True)


# ============================================================
# Flujo completo (para uso desde script / CLI)
# ============================================================

def migrar(url, yt_key, ids=None, year_from=None, year_to=None, distributors=None,
           quiere_planilla=True, quiere_audio=False, quiere_portadas=True,
           tidal_session=None, out_path=None, log=print):
    """Corre los 4 pasos de una. Devuelve dict con el resultado."""
    prods, artista, _ = relevar_catalogo(url, yt_key, progress=log)
    seleccion = productos_mod.filter_products(
        prods, ids=ids, year_from=year_from, year_to=year_to, distributors=distributors,
    )
    if not seleccion:
        raise relevar_core.RelevarError("La selección quedó vacía: revisá los filtros.")
    log(f"[seleccion] {len(seleccion)} de {len(prods)} productos")

    seleccion, dir_audio, entorno = preparar(
        seleccion, artista, quiere_planilla=quiere_planilla, quiere_audio=quiere_audio,
        quiere_portadas=quiere_portadas, tidal_session=tidal_session, log=log,
    )
    con_tidal = bool(tidal_session and tidal_session.conectada)
    ruta, tam = empaquetar(
        seleccion, artista, out_path=out_path, entorno=entorno, con_tidal=con_tidal,
        incluir_planilla=quiere_planilla, incluir_audio=quiere_audio,
        incluir_portadas=quiere_portadas, log=log,
    )
    limpiar(dir_audio)
    return {
        "artista": artista,
        "zip": ruta,
        "bytes": tam,
        "productos": len(seleccion),
        "resumen": productos_mod.summarize(seleccion),
    }

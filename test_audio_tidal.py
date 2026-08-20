# -*- coding: utf-8 -*-
"""Test del contrato con la librería tiddl (sin credenciales, sin red).

Corré:  python test_audio_tidal.py
Sale 0 si todo pasa, 1 si algo falla. Si tiddl no está instalado, se saltea.

Existe por un bug que llegó al usuario: audio.py importaba `TidalApi` cuando la
clase se llama `TidalAPI`. La conexión de Tidal se veía exitosa, y después no
bajaba ningún audio sin explicación, porque el import fallaba recién al usar la
sesión. Ninguna prueba lo agarró porque todo el módulo de audio dependía de
credenciales que no se pueden poner en un test.

La idea acá es verificar, SIN credenciales, que todo lo que audio.py le pide a
tiddl exista y tenga la forma esperada: nombres de clase, métodos, firmas y los
campos que leemos de las respuestas. Es lo que separa "la librería cambió" de un
misterio en tiempo de ejecución.
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    try:
        import tiddl  # noqa: F401
    except ImportError:
        print("OK - tiddl no está instalado, test salteado (es opcional)")
        return 0

    import audio

    fails = []

    def check(name, cond, detalle=""):
        if not cond:
            fails.append(f"  [{name}] falló {detalle}")

    # ---- las clases que importa audio.py ----
    try:
        from tiddl.core.api import TidalAPI, TidalClient
        check("api.TidalAPI", True)
    except ImportError as e:
        fails.append(f"  [api.import] no pude importar TidalAPI/TidalClient: {e}")
        print("FALLARON:"); print("\n".join(fails)); return 1

    try:
        from tiddl.core.auth import AuthAPI, AuthClientError
    except ImportError as e:
        fails.append(f"  [auth.import] no pude importar AuthAPI/AuthClientError: {e}")
        print("FALLARON:"); print("\n".join(fails)); return 1

    # ---- constructores ----
    p_api = list(inspect.signature(TidalAPI.__init__).parameters)
    check("TidalAPI.firma", p_api[:4] == ["self", "client", "user_id", "country_code"],
          f"params={p_api}")
    p_cli = list(inspect.signature(TidalClient.__init__).parameters)
    for req in ("token", "cache_name"):
        check(f"TidalClient.param:{req}", req in p_cli, f"params={p_cli}")

    # ---- métodos de la API que usa audio.py ----
    for m in ("get_search", "get_artist_albums", "get_album_items",
              "get_track", "get_track_stream"):
        check(f"TidalAPI.{m}", hasattr(TidalAPI, m))

    # get_artist_albums tiene que aceptar limit/offset/filter: sin paginar y sin
    # pedir EPSANDSINGLES el índice sale con 10 álbumes y ningún single.
    p = inspect.signature(TidalAPI.get_artist_albums).parameters
    for req in ("limit", "offset", "filter"):
        check(f"get_artist_albums.{req}", req in p, f"params={list(p)}")
    # Y el default es bajo a propósito: por eso audio.py DEBE pasar limit.
    check("get_artist_albums.default_bajo",
          p["limit"].default is not inspect.Parameter.empty and p["limit"].default <= 50,
          f"default={p['limit'].default}: si subió, revisar PAGINA_TIDAL")

    p = inspect.signature(TidalAPI.get_album_items).parameters
    for req in ("limit", "offset"):
        check(f"get_album_items.{req}", req in p, f"params={list(p)}")

    p = inspect.signature(TidalAPI.get_track_stream).parameters
    check("get_track_stream.quality", "quality" in p, f"params={list(p)}")

    # ---- métodos de autenticación ----
    for m in ("get_device_auth", "get_auth", "refresh_token"):
        check(f"AuthAPI.{m}", hasattr(AuthAPI, m))

    # AuthClientError.error: audio.py lo usa para distinguir "pendiente".
    e = AuthClientError(status=400, error="authorization_pending")
    check("AuthClientError.error", getattr(e, "error", None) == "authorization_pending")

    # ---- campos de los modelos que leemos ----
    from tiddl.core.api.api import AlbumItems, ArtistAlbumsItems, Search
    from tiddl.core.api.models.base import AlbumItems as BaseAlbumItems
    from tiddl.core.api.models.resources import Album, Track

    for campo in ("items", "totalNumberOfItems"):
        check(f"ArtistAlbumsItems.{campo}", campo in ArtistAlbumsItems.model_fields)
        check(f"AlbumItems.{campo}", campo in AlbumItems.model_fields)
    check("Search.artists", "artists" in Search.model_fields)
    check("Search.Artists.items", "items" in Search.Artists.model_fields)

    # Album: sacamos upc y title para el índice.
    for campo in ("id", "title", "upc"):
        check(f"Album.{campo}", campo in Album.model_fields)
    # Track: isrc y el orden real, que YouTube no da.
    for campo in ("id", "title", "isrc", "trackNumber", "volumeNumber", "mediaMetadata"):
        check(f"Track.{campo}", campo in Track.model_fields)
    # Los items del álbum vienen envueltos en .item
    check("TrackItem.item", "item" in BaseAlbumItems.TrackItem.model_fields)

    # ---- utilidades de descarga ----
    try:
        from tiddl.core.metadata import add_track_metadata  # noqa: F401
        from tiddl.core.utils import get_track_stream_data  # noqa: F401
        from tiddl.core.utils.ffmpeg import extract_flac  # noqa: F401
    except ImportError as exc:
        fails.append(f"  [utils.import] {exc}")

    # ---- que audio.py pagine de verdad ----
    src = inspect.getsource(audio.construir_indice_isrc)
    check("audio.pide_EPSANDSINGLES", "EPSANDSINGLES" in src,
          "sin esto los singles y EPs nunca entran al índice")
    check("audio.pagina_albumes", "offset" in src and "totalNumberOfItems" in src,
          "sin paginar, la discografía queda cortada en el ítem 10")
    # Esto se ejecuta de verdad: si el nombre de la clase está mal, tira
    # ImportError acá y no a mitad de una descarga.
    try:
        ClaseAPI, ClaseCliente = audio.clases_tidal()
        check("audio.clases_tidal", ClaseAPI is TidalAPI and ClaseCliente is TidalClient,
              f"devolvio {ClaseAPI!r}, {ClaseCliente!r}")
    except Exception as exc:
        fails.append(f"  [audio.clases_tidal] no pudo importar las clases: {exc}")

    if fails:
        print("FALLARON:")
        print("\n".join(fails))
        return 1
    print("OK - contrato con la libreria tiddl")
    return 0


if __name__ == "__main__":
    sys.exit(main())

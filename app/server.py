"""
Backend de la app de escritorio: servidor HTTP local + API JSON.

Usa `http.server` de la biblioteca estándar a propósito, en vez de FastAPI o
Flask. Es una app local de un solo usuario, así que no necesitamos ni ASGI ni
workers, y en cambio ganamos lo que más importa para empaquetar: **cero
dependencias nuevas**. El ejecutable queda chico y PyInstaller no tiene que
resolver los imports dinámicos de uvicorn, que son la causa habitual de que un
binario ande en desarrollo y falle empaquetado.

El servidor sólo escucha en 127.0.0.1: no queda expuesto en la red.

Endpoints:
  GET  /                      la interfaz
  GET  /<archivo>             estáticos (js, css, assets)
  GET  /api/config            capacidades del entorno y estado de la clave
  POST /api/relevar           {url, with_codes} -> {job}      (asincrónico)
  POST /api/validar           {seleccion} -> hallazgos          (sincrónico)
  POST /api/preparar          {ids, opciones} -> {job}         (asincrónico)
  GET  /api/job/<id>          estado del trabajo
  POST /api/job/<id>/cancelar
  GET  /api/descargar/<id>    baja el ZIP (streaming)
  POST /api/tidal/...         conexión opcional de Tidal
"""

import json
import mimetypes
import os
import posixpath
import re
import shutil
import sys
import tempfile
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# El paquete corre tanto desde el repo como desde el binario de PyInstaller.
_AQUI = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_AQUI)
for _p in (_RAIZ, _AQUI):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import audio as audio_mod                     # noqa: E402
import migrar_core as M                       # noqa: E402
import productos as P                         # noqa: E402
import relevar_core as R                      # noqa: E402
import validar as V                           # noqa: E402
from jobs import Registry                     # noqa: E402

VERSION = "1.0.0"

# El módulo de audio viene apagado. Se prende con MIGRADOR_AUDIO=1 y aun así
# sólo aparece si el entorno lo soporta.
AUDIO_HABILITADO = os.environ.get("MIGRADOR_AUDIO", "").strip().lower() in ("1", "true", "si", "sí")

def _base_recursos():
    """Carpeta donde viven los archivos de la interfaz.

    Empaquetado con PyInstaller los datos se extraen a `sys._MEIPASS`, que no
    coincide con la ubicación del módulo. Sin este ajuste la app anda en
    desarrollo y sirve 404 en el ejecutable.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", _AQUI)
    return _AQUI


WEB_DIR = os.path.join(_base_recursos(), "web")
MAX_BODY = 8 * 1024 * 1024          # 8 MB: los payloads son listas de ids


def dir_datos():
    """Carpeta de la app en el home del usuario, para la clave y los temporales."""
    base = os.path.join(os.path.expanduser("~"), ".migrador-catalogos")
    os.makedirs(base, exist_ok=True)
    return base


# ============================================================
# Clave de YouTube
# ============================================================

def leer_clave():
    """Busca la clave en el entorno y después en el archivo del usuario."""
    k = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if k:
        return k
    p = os.path.join(dir_datos(), "config.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return (json.load(f).get("youtube_api_key") or "").strip()
        except (OSError, ValueError):
            return ""
    return ""


def guardar_clave(clave):
    """Guarda la clave en el home del usuario. Queda sólo en su máquina."""
    p = os.path.join(dir_datos(), "config.json")
    datos = {}
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError):
            datos = {}
    datos["youtube_api_key"] = (clave or "").strip()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2)
    try:
        os.chmod(p, 0o600)          # en Windows es no-op, en Unix protege el archivo
    except OSError:
        pass


# ============================================================
# Estado de la sesión
# ============================================================

class Estado:
    """Estado del catálogo en curso. Vive en memoria: al cerrar la app se va."""

    def __init__(self):
        self.productos = []
        self.artista = ""
        self.tidal = None
        self.zips = {}              # job_id -> ruta del zip
        self.temporales = []
        self.lock = threading.Lock()

    def por_ids(self, ids):
        if not ids:
            return list(self.productos)
        return P.filter_products(self.productos, ids=ids)

    def limpiar(self):
        if self.tidal:
            try:
                self.tidal.close()
            except Exception:
                pass
            self.tidal = None
        for d in self.temporales:
            shutil.rmtree(d, ignore_errors=True)
        self.temporales = []
        for ruta in self.zips.values():
            shutil.rmtree(os.path.dirname(ruta), ignore_errors=True)
        self.zips = {}


ESTADO = Estado()
JOBS = Registry()


# ============================================================
# Serialización para el frontend
# ============================================================

def _mmss(seg):
    seg = int(seg or 0)
    return f"{seg // 60}:{seg % 60:02d}"


def producto_json(p):
    """Un producto tal como lo consume la tabla del frontend."""
    return {
        "id": p["product_id"],
        "titulo": p.get("title", ""),
        "tipo": p.get("kind", ""),
        "anio": p.get("release_year") or "",
        "fecha": p.get("release_date") or "",
        "upc": p.get("upc") or "",
        "sello": p.get("label") or "",
        "distribuidora": p.get("distributor") or "",
        "tracks": p.get("track_count", 0),
        "views": p.get("total_views", 0),
        "orden_estimado": bool(p.get("order_unconfirmed")),
        "con_isrc": sum(1 for t in p.get("tracks", []) if t.get("isrc")),
        "detalle": [{
            "n": t.get("track_number") or "",
            "titulo": t.get("track", ""),
            "isrc": t.get("isrc") or "",
            "duracion": _mmss(t.get("duration_s")),
            "views": t.get("views", 0),
            "url": t.get("url", ""),
        } for t in p.get("tracks", [])],
    }


def catalogo_json(productos, artista):
    desde, hasta = P.year_range(productos)
    return {
        "artista": artista,
        "productos": [producto_json(p) for p in productos],
        "resumen": P.summarize(productos),
        "filtros": {
            "distribuidoras": P.distributor_options(productos),
            "anio_min": desde,
            "anio_max": hasta,
        },
    }


# ============================================================
# Handlers de la API
# ============================================================

def api_config():
    ent = audio_mod.verificar_entorno()
    return {
        "version": VERSION,
        "tiene_clave": bool(leer_clave()),
        "audio_habilitado": AUDIO_HABILITADO,
        "entorno": ent,
        "tidal_conectada": bool(ESTADO.tidal and ESTADO.tidal.conectada),
        "catalogo_cargado": bool(ESTADO.productos),
    }


def api_guardar_clave(body):
    clave = (body.get("clave") or "").strip()
    if not clave:
        raise ValueError("Pegá la clave de la API de YouTube.")
    # Validación real: pegamos una consulta mínima antes de darla por buena, así
    # el usuario se entera acá y no a mitad de un relevamiento.
    try:
        R.api_get("channels", {"part": "id", "id": "UC_x5XG1OV2P6uZZ5FSM9Ttw"}, clave)
    except Exception as e:                       # noqa: BLE001
        raise ValueError(f"La clave no funcionó: {e}")
    guardar_clave(clave)
    return {"ok": True}


def api_relevar(body):
    url = (body.get("url") or "").strip()
    if not url:
        raise ValueError("Pegá el link del canal de YouTube.")
    clave = leer_clave()
    if not clave:
        raise ValueError("Falta configurar la clave de la API de YouTube.")
    con_codigos = bool(body.get("con_codigos", True))

    def trabajo(job):
        prods, artista, _ = M.relevar_catalogo(
            url, clave, with_codes=con_codigos,
            progress=lambda m, f=None: job.avance(m, f))
        with ESTADO.lock:
            ESTADO.productos = prods
            ESTADO.artista = artista
        job.avance(f"{len(prods)} productos encontrados.", 1.0)
        return catalogo_json(prods, artista)

    return {"job": JOBS.lanzar("relevar", trabajo).a_dict()}


def api_validar(body):
    sel = ESTADO.por_ids(body.get("ids"))
    if not sel:
        raise ValueError("No hay productos seleccionados.")
    res = V.validar(sel, ESTADO.artista)
    return {
        "apto": res["apto"],
        "resumen": res["resumen"],
        "hallazgos": res["hallazgos"],
    }


def api_preparar(body):
    ids = body.get("ids") or []
    sel = ESTADO.por_ids(ids)
    if not sel:
        raise ValueError("No hay productos seleccionados.")

    quiere_planilla = bool(body.get("planilla", True))
    quiere_portadas = bool(body.get("portadas", True))
    quiere_audio = bool(body.get("audio", False)) and AUDIO_HABILITADO
    if not (quiere_planilla or quiere_portadas or quiere_audio):
        raise ValueError("Elegí al menos una cosa para descargar.")

    artista = ESTADO.artista
    ses = ESTADO.tidal if quiere_audio else None

    def trabajo(job):
        # Sobre copias: preparar() agrega bytes de portada y rutas de audio a los
        # productos, y no queremos que el catálogo en memoria se llene de eso
        # después de cada descarga.
        copias = [dict(p, tracks=[dict(t) for t in p["tracks"]]) for p in sel]

        job.avance("Preparando…", 0.05)
        _, dir_audio, ent = M.preparar(
            copias, artista, quiere_planilla=quiere_planilla,
            quiere_audio=quiere_audio, quiere_portadas=quiere_portadas,
            tidal_session=ses, log=lambda m: job.avance(m))
        if dir_audio:
            ESTADO.temporales.append(dir_audio)

        job.avance("Armando el ZIP…", 0.9)
        carpeta = tempfile.mkdtemp(prefix="migrador_zip_")
        destino = os.path.join(carpeta, f"{R.slugify(artista)}-migracion.zip")
        ruta, tam = M.empaquetar(
            copias, artista, out_path=destino, entorno=ent,
            con_tidal=bool(ses and ses.conectada),
            incluir_planilla=quiere_planilla, incluir_audio=quiere_audio,
            incluir_portadas=quiere_portadas, log=lambda m: job.avance(m))

        ESTADO.zips[job.id] = ruta
        if dir_audio:
            M.limpiar(dir_audio)

        val = V.validar(copias, artista)
        job.avance("Paquete listo.", 1.0)
        return {
            "archivo": os.path.basename(ruta),
            "bytes": tam,
            "descarga": f"/api/descargar/{job.id}",
            "validacion": {"apto": val["apto"], "resumen": val["resumen"],
                           "hallazgos": val["hallazgos"]},
            "portadas": sum(1 for p in copias if p.get("cover_bytes")),
            "productos": len(copias),
        }

    return {"job": JOBS.lanzar("preparar", trabajo).a_dict()}


def api_tidal_iniciar():
    if not AUDIO_HABILITADO:
        raise ValueError("El módulo de audio está desactivado.")
    ses = audio_mod.TidalSession()
    info = ses.iniciar_login()
    ESTADO.tidal = ses
    # Sólo lo necesario para que el usuario complete el login en el sitio de Tidal.
    return {"url": info["url"], "codigo": info["user_code"],
            "device_code": info["device_code"], "expira_en": info["expires_in"]}


def api_tidal_confirmar(body):
    if not ESTADO.tidal:
        raise ValueError("No hay una conexión de Tidal en curso.")
    estado = ESTADO.tidal.poll_login((body.get("device_code") or "").strip())
    return {"estado": estado, "conectada": bool(ESTADO.tidal.conectada)}


def api_tidal_desconectar():
    if ESTADO.tidal:
        ESTADO.tidal.close()
        ESTADO.tidal = None
    return {"ok": True}


RUTAS_POST = {
    "/api/clave": api_guardar_clave,
    "/api/relevar": api_relevar,
    "/api/validar": api_validar,
    "/api/preparar": api_preparar,
    "/api/tidal/confirmar": api_tidal_confirmar,
}
RUTAS_POST_SIN_BODY = {
    "/api/tidal/iniciar": api_tidal_iniciar,
    "/api/tidal/desconectar": api_tidal_desconectar,
}


# ============================================================
# Servidor
# ============================================================

class Handler(BaseHTTPRequestHandler):
    server_version = f"Migrador/{VERSION}"
    protocol_version = "HTTP/1.1"
    # Con HTTP/1.1 las conexiones quedan vivas esperando el pedido siguiente. Sin
    # timeout, una conexion abandonada deja el hilo colgado y el cierre puede
    # llegar justo cuando el cliente va a reusarla (en Windows eso aparece como
    # WinError 10053 del lado del cliente). Con timeout se cierran ordenadamente.
    timeout = 60

    # ---- utilidades ----

    def _json(self, datos, codigo=HTTPStatus.OK):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _error(self, mensaje, codigo=HTTPStatus.BAD_REQUEST):
        self._json({"error": str(mensaje)}, codigo)

    def _leer_body(self):
        largo = int(self.headers.get("Content-Length") or 0)
        if largo <= 0:
            return {}
        if largo > MAX_BODY:
            raise ValueError("El pedido es demasiado grande.")
        crudo = self.rfile.read(largo)
        try:
            return json.loads(crudo.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("El pedido no es JSON válido.")

    def log_message(self, formato, *args):
        # Silencio: el log de acceso de http.server ensucia la consola de la app.
        pass

    # ---- GET ----

    def do_GET(self):
        ruta = urllib.parse.urlparse(self.path).path

        if ruta == "/api/config":
            return self._json(api_config())

        if ruta == "/api/catalogo":
            # Permite recuperar el catálogo si se recarga la página: el
            # relevamiento cuesta cuota de YouTube y no queremos repetirlo por
            # un F5 accidental.
            if not ESTADO.productos:
                return self._error("No hay un catálogo cargado.", HTTPStatus.NOT_FOUND)
            return self._json(catalogo_json(ESTADO.productos, ESTADO.artista))

        m = re.fullmatch(r"/api/job/([0-9a-f]{6,32})", ruta)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self._error("Ese trabajo ya no existe.", HTTPStatus.NOT_FOUND)
            return self._json(job.a_dict(con_log=True))

        m = re.fullmatch(r"/api/descargar/([0-9a-f]{6,32})", ruta)
        if m:
            return self._descargar(m.group(1))

        return self._estatico(ruta)

    def _descargar(self, job_id):
        ruta = ESTADO.zips.get(job_id)
        if not ruta or not os.path.exists(ruta):
            return self._error("El paquete ya no está disponible. Generalo de nuevo.",
                               HTTPStatus.NOT_FOUND)
        tam = os.path.getsize(ruta)
        nombre = os.path.basename(ruta)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{nombre}"')
        self.send_header("Content-Length", str(tam))
        self.end_headers()
        # En bloques: un catálogo con audio puede pesar varios GB y no entra en RAM.
        try:
            with open(ruta, "rb") as f:
                shutil.copyfileobj(f, self.wfile, length=1024 * 256)
        except (BrokenPipeError, ConnectionError):
            # El usuario canceló la descarga. No es un error nuestro; cortamos la
            # conexión y listo, sin ensuciar la consola con un traceback.
            self.close_connection = True

    def _estatico(self, ruta):
        if ruta in ("/", "/index.html"):
            relativo = "index.html"
        else:
            # Normalizamos para que no se pueda salir de WEB_DIR con "..".
            limpio = posixpath.normpath(urllib.parse.unquote(ruta)).lstrip("/")
            if limpio.startswith("..") or os.path.isabs(limpio):
                return self._error("No encontrado.", HTTPStatus.NOT_FOUND)
            relativo = limpio

        destino = os.path.normpath(os.path.join(WEB_DIR, relativo))
        if not destino.startswith(os.path.normpath(WEB_DIR)):
            return self._error("No encontrado.", HTTPStatus.NOT_FOUND)
        if not os.path.isfile(destino):
            return self._error("No encontrado.", HTTPStatus.NOT_FOUND)

        tipo = mimetypes.guess_type(destino)[0] or "application/octet-stream"
        with open(destino, "rb") as f:
            cuerpo = f.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        # Sin cache: si no, una actualización de la app sirve el JS viejo.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    # ---- POST ----

    def do_POST(self):
        ruta = urllib.parse.urlparse(self.path).path
        try:
            if ruta in RUTAS_POST_SIN_BODY:
                return self._json(RUTAS_POST_SIN_BODY[ruta]())

            m = re.fullmatch(r"/api/job/([0-9a-f]{6,32})/cancelar", ruta)
            if m:
                job = JOBS.get(m.group(1))
                if not job:
                    return self._error("Ese trabajo ya no existe.", HTTPStatus.NOT_FOUND)
                job.cancelar()
                return self._json({"ok": True})

            fn = RUTAS_POST.get(ruta)
            if not fn:
                return self._error("No encontrado.", HTTPStatus.NOT_FOUND)
            return self._json(fn(self._leer_body()))

        except ValueError as e:
            # Errores esperables y mostrables al usuario.
            return self._error(e, HTTPStatus.BAD_REQUEST)
        except R.RelevarError as e:
            return self._error(e, HTTPStatus.UNPROCESSABLE_ENTITY)
        except Exception as e:                        # noqa: BLE001
            return self._error(f"Error inesperado: {e}", HTTPStatus.INTERNAL_SERVER_ERROR)


def crear_servidor(puerto=0):
    """Servidor atado a localhost. puerto=0 deja que el sistema elija uno libre,
    así nunca choca con algo que ya esté escuchando."""
    return ThreadingHTTPServer(("127.0.0.1", puerto), Handler)


def main(puerto=0, abrir=True):
    srv = crear_servidor(puerto)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    print(f"Migrador de Catálogos v{VERSION}")
    print(f"Servidor local: {url}")
    if abrir:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando…")
    finally:
        ESTADO.limpiar()
        srv.server_close()


if __name__ == "__main__":
    main()

"""
Descarga de audio para la migración, en dos niveles de calidad claramente
separados y etiquetados.

  NIVEL A — Tidal (FLAC lossless).  Requiere que el usuario conecte SU PROPIA
    cuenta paga de Tidal. Es el único audio apto para entregar a una
    distribuidora, porque es el máster distribuido sin pérdida.

  NIVEL B — YouTube (lossy).  No requiere credenciales, sirve para cualquiera.
    Es audio ya comprimido por YouTube (Opus/AAC ~128-160 kbps): vale como
    referencia, inventario o verificación, NUNCA como entrega.

Dos decisiones de diseño que sostienen la honestidad de la herramienta:

1. **Verificamos el formato real, no el pedido.** Tidal puede servir AAC-en-MP4
   para tracks que no tienen máster lossless, incluso cuando pedís LOSSLESS. Así
   que cada archivo se etiqueta por lo que *efectivamente* llegó (mirando el
   codec), no por lo que pedimos. Un `.m4a` devuelto donde esperábamos `.flac`
   se reporta como lossy, no como máster.

2. **Aislamiento por usuario.** La herramienta la usan clientes externos, así
   que cada sesión tiene su token en memoria y su cache en un directorio propio
   que se borra al cerrar. No se escribe token a disco ni se toca el `~/.tiddl`
   global (que es de un solo usuario por diseño). Del perfil de Tidal guardamos
   sólo `user_id` y `country_code`, que es lo que la API necesita: nada de
   email, nombre, dirección ni teléfono, aunque el login los devuelva.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

# Formatos que consideramos aptos para entrega (lossless real).
FORMATOS_LOSSLESS = {".flac"}

ETIQUETA_LOSSLESS = "Tidal FLAC (lossless) — apto para entrega"
ETIQUETA_LOSSY_TIDAL = "Tidal AAC (lossy — sin máster lossless en Tidal) — NO apto para entrega"
ETIQUETA_YOUTUBE = "YouTube (lossy) — REFERENCIA, NO apto para entrega"

# Tidal corta si le pegamos muy en paralelo, y además el límite de streams
# concurrentes por cuenta es bajo. Con 2 hilos va estable.
TIDAL_WORKERS = 2
YT_WORKERS = 3

# Tamaño de página al recorrer la discografía. La API topea en 100 por pedido.
PAGINA_TIDAL = 100


# ============================================================
# Chequeo de entorno
# ============================================================

def _existe(cmd):
    return shutil.which(cmd) is not None


def _runtime_js():
    """Runtime de JavaScript para yt-dlp, o None.

    YouTube exige resolver un desafío en JavaScript para armar las URLs de
    descarga. Sin un runtime, yt-dlp avisa que la extracción está deprecada, cae
    a un cliente alternativo y las URLs devuelven 403. Deno es el único que
    habilita solo; si hay Node lo pasamos explícitamente.
    """
    for nombre in ("deno", "node", "bun"):
        if _existe(nombre):
            return nombre
    return None


def verificar_entorno():
    """Qué capacidades están disponibles en esta máquina.

    Se llama antes de ofrecer opciones en la UI: no tiene sentido ofrecer FLAC
    si falta ffmpeg, ni referencia de YouTube si falta yt-dlp.
    """
    try:
        import tiddl  # noqa: F401
        tiene_tiddl = True
    except ImportError:
        tiene_tiddl = False

    try:
        import yt_dlp  # noqa: F401
        tiene_ytdlp = True
    except ImportError:
        tiene_ytdlp = _existe("yt-dlp")

    ffmpeg = _existe("ffmpeg")
    return {
        "ffmpeg": ffmpeg,
        "ffprobe": _existe("ffprobe"),
        "js_runtime": _runtime_js(),
        "tiddl": tiene_tiddl,
        "yt_dlp": tiene_ytdlp,
        # FLAC necesita las dos cosas: tiddl para bajar y ffmpeg para extraer.
        "puede_flac": tiene_tiddl and ffmpeg,
        "puede_referencia": tiene_ytdlp and ffmpeg,
    }


def clases_tidal():
    """Devuelve (TidalAPI, TidalClient) de tiddl.

    El import está acá y no dentro del método que lo usa para poder verificarlo
    en un test sin credenciales. Nació de un bug real: decía `TidalApi` y la
    clase es `TidalAPI`. Como el import ocurría recién al usar la sesión, la
    conexión de Tidal se veía exitosa y después no bajaba ningún audio, sin
    ninguna pista de por qué.
    """
    from tiddl.core.api import TidalAPI, TidalClient
    return TidalAPI, TidalClient


# ============================================================
# Sesión de Tidal — aislada por usuario
# ============================================================

class TidalSession:
    """Sesión de Tidal de UN usuario. Token en memoria, cache en dir temporal
    propio, todo borrado en `close()`.

    Uso:
        s = TidalSession()
        info = s.iniciar_login()          # mostrarle el código al usuario
        ... s.esperar_login(info)         # poll hasta que confirme en tidal.com
        s.close()                         # borra token y cache
    """

    def __init__(self):
        self._token = None
        self._refresh_token = None
        self._expires_at = 0
        self.user_id = None
        self.country_code = None
        # Cache aislado: requests_cache guarda respuestas de la API, que pueden
        # incluir datos de la cuenta. No se comparte entre usuarios.
        self._cache_dir = tempfile.mkdtemp(prefix="migrador_tidal_")
        self._api = None

    # ---- login por device-code (la contraseña la pone el usuario en Tidal) ----

    def iniciar_login(self):
        """Arranca el flujo device-code. Devuelve los datos para mostrarle al
        usuario a dónde ir y qué código poner. Nunca pedimos su contraseña."""
        from tiddl.core.auth import AuthAPI
        d = AuthAPI().get_device_auth()
        return {
            "device_code": d.deviceCode,
            "user_code": d.userCode,
            "url": f"https://{d.verificationUriComplete}",
            "interval": d.interval,
            "expires_in": d.expiresIn,
        }

    def poll_login(self, device_code):
        """Consulta una vez si el usuario ya confirmó.
        Devuelve 'ok', 'pendiente', o un mensaje de error."""
        from tiddl.core.auth import AuthAPI, AuthClientError
        try:
            auth = AuthAPI().get_auth(device_code)
        except AuthClientError as e:
            # authorization_pending: la persona todavía no confirmó.
            # slow_down: confirmamos demasiado seguido; tampoco es un error, sólo
            # hay que esperar un poco más. Devolver "error: slow_down" hacía que
            # la interfaz mostrara un código crudo por algo que estaba yendo bien.
            if e.error in ("authorization_pending", "slow_down"):
                return "pendiente"
            return f"error: {e.error}"

        # Guardamos SÓLO lo que la API necesita. El resto del perfil (email,
        # nombre, dirección, teléfono, cumpleaños) se descarta a propósito.
        self._token = auth.access_token
        self._refresh_token = getattr(auth, "refresh_token", None)
        self._expires_at = int(time.time()) + int(auth.expires_in)
        self.user_id = str(auth.user_id)
        self.country_code = auth.user.countryCode
        self._api = None
        return "ok"

    def esperar_login(self, info, log=print):
        """Poll hasta que el usuario confirme o expire el código."""
        limite = time.time() + info["expires_in"]
        while time.time() < limite:
            time.sleep(max(int(info.get("interval") or 2), 1))
            estado = self.poll_login(info["device_code"])
            if estado == "ok":
                log("[tidal] cuenta conectada")
                return True
            if estado != "pendiente":
                log(f"[tidal] {estado}")
                return False
        log("[tidal] el código expiró sin confirmación")
        return False

    @property
    def conectada(self):
        return bool(self._token) and time.time() < self._expires_at

    def _refrescar_si_hace_falta(self):
        if self._token and time.time() >= self._expires_at - 60 and self._refresh_token:
            from tiddl.core.auth import AuthAPI
            try:
                auth = AuthAPI().refresh_token(self._refresh_token)
                self._token = auth.access_token
                self._expires_at = int(time.time()) + int(auth.expires_in)
                self._api = None
            except Exception:
                pass

    @property
    def api(self):
        """TidalApi de esta sesión, con cache propio."""
        if not self._token:
            raise RuntimeError("La cuenta de Tidal no está conectada.")
        self._refrescar_si_hace_falta()
        if self._api is None:
            TidalAPI, TidalClient = clases_tidal()
            client = TidalClient(
                token=self._token,
                cache_name=os.path.join(self._cache_dir, "api_cache"),
            )
            self._api = TidalAPI(client, self.user_id, self.country_code)
        return self._api

    def close(self):
        """Borra token y cache. Llamar siempre al terminar la sesión."""
        self._token = self._refresh_token = None
        self._expires_at = 0
        self.user_id = self.country_code = self._api = None
        shutil.rmtree(self._cache_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ============================================================
# Índice por ISRC — el corazón del matcheo exacto
# ============================================================

def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


class TidalAuthError(RuntimeError):
    """La sesión de Tidal no sirve (token vencido o revocado).

    Se distingue de "no encontré al artista" a propósito: son dos problemas con
    soluciones distintas, y confundirlos manda al usuario a buscar por el lado
    equivocado. Si el token venció hay que reconectar la cuenta, no revisar el
    nombre del artista.
    """


def buscar_artista_tidal(session, nombre):
    """Encuentra el artist_id en Tidal. Devuelve (id, nombre) o (None, None).

    Lanza TidalAuthError si el problema es la sesión y no la búsqueda.
    """
    try:
        res = session.api.get_search(nombre)
    except Exception as e:
        texto = str(e).lower()
        if "401" in texto or "token" in texto or "unauthorized" in texto:
            raise TidalAuthError(
                "La sesión de Tidal no es válida o venció. Volvé a conectar la cuenta."
            ) from e
        return None, None
    artistas = getattr(res, "artists", None)
    items = getattr(artistas, "items", None) or []
    if not items:
        return None, None

    objetivo = _norm(nombre)
    for a in items:                      # coincidencia exacta primero
        if _norm(getattr(a, "name", "")) == objetivo:
            return getattr(a, "id", None), getattr(a, "name", "")
    a = items[0]                         # si no, el primero que ranquea Tidal
    return getattr(a, "id", None), getattr(a, "name", "")


def construir_indice_isrc(session, artista, log=print):
    """Baja la discografía completa del artista en Tidal y arma un índice
    {ISRC -> datos del track}.

    Esto es lo que permite matchear por código en vez de por título: el ISRC
    identifica la grabación de forma única, así que un match por ISRC es exacto.
    """
    try:
        artist_id, nombre_tidal = buscar_artista_tidal(session, artista)
    except TidalAuthError as e:
        log(f"[tidal] {e}")
        return {}, None
    if not artist_id:
        log(f"[tidal] no encontré a '{artista}' en el catálogo de Tidal. "
            "Si el nombre difiere del de Tidal, el match por ISRC no se puede armar.")
        return {}, None

    log(f"[tidal] artista: {nombre_tidal} (id {artist_id})")
    indice = {}
    items = []
    # Dos cosas que hay que pedir explícitamente:
    #  - filter: por defecto la API devuelve SÓLO "ALBUMS". Sin pedir también
    #    "EPSANDSINGLES" ningún single ni EP entra al índice, que en un catálogo
    #    DIY es la mayor parte del material.
    #  - paginación: el límite por defecto es 10 álbumes (máximo 100 por página),
    #    así que sin paginar una discografía grande queda cortada en el ítem 10.
    for filtro in ("ALBUMS", "EPSANDSINGLES"):
        offset = 0
        while True:
            try:
                pagina = session.api.get_artist_albums(
                    artist_id, limit=PAGINA_TIDAL, offset=offset, filter=filtro)
            except Exception as e:
                log(f"[tidal] no pude listar {filtro}: {e}")
                break
            lote = getattr(pagina, "items", None) or []
            items.extend(lote)
            total = getattr(pagina, "totalNumberOfItems", 0) or 0
            offset += len(lote)
            if not lote or offset >= total:
                break
    log(f"[tidal] releases en la discografía: {len(items)}")

    for al in items:
        album_id = getattr(al, "id", None)
        if not album_id:
            continue
        tracks = []
        # Idem con los tracks: el límite por defecto es 20 y un álbum largo o un
        # recopilado se cortaría por la mitad.
        offset = 0
        while True:
            try:
                pagina = session.api.get_album_items(
                    album_id, limit=PAGINA_TIDAL, offset=offset)
            except Exception:
                break
            lote = getattr(pagina, "items", None) or []
            tracks.extend(lote)
            total = getattr(pagina, "totalNumberOfItems", 0) or 0
            offset += len(lote)
            if not lote or offset >= total:
                break

        for it in tracks:
            t = getattr(it, "item", it)
            isrc = (getattr(t, "isrc", "") or "").upper().strip()
            if not isrc:
                continue
            indice[isrc] = {
                "track_id": getattr(t, "id", None),
                "album_id": album_id,
                "album_title": getattr(al, "title", ""),
                "upc": getattr(al, "upc", "") or "",
                "track_number": getattr(t, "trackNumber", None),
                "volume_number": getattr(t, "volumeNumber", None),
                "title": getattr(t, "title", ""),
                # Etiquetas de calidad que declara Tidal (a confirmar al bajar).
                "tags": list(getattr(getattr(t, "mediaMetadata", None), "tags", []) or []),
            }

    log(f"[tidal] índice armado: {len(indice)} ISRC en {len(items)} releases")
    return indice, artist_id


def matchear_por_isrc(productos, indice, log=print):
    """Cruza el catálogo relevado contra el índice de Tidal por ISRC.

    Como efecto secundario completa el número de track real desde Tidal, que
    YouTube no da: eso resuelve el orden provisorio de la agrupación.
    """
    hit = miss = sin_isrc = 0
    for p in productos:
        encontrados = 0
        for t in p["tracks"]:
            isrc = (t.get("isrc") or "").upper().strip()
            if not isrc:
                t["tidal"] = None
                sin_isrc += 1
                continue
            m = indice.get(isrc)
            t["tidal"] = m
            if m:
                hit += 1
                encontrados += 1
                if m.get("track_number"):
                    t["track_number"] = m["track_number"]
                if m.get("upc") and not p.get("upc"):
                    p["upc"] = m["upc"]
            else:
                miss += 1
        # Si Tidal nos dio el orden real, dejamos de marcarlo como provisorio.
        if encontrados == p["track_count"] and all(
            t.get("tidal", {}) and t["tidal"].get("track_number") for t in p["tracks"]
        ):
            p["tracks"].sort(key=lambda t: (
                t["tidal"].get("volume_number") or 1, t["tidal"]["track_number"]
            ))
            p["order_unconfirmed"] = False
        p["tidal_cobertura"] = f"{encontrados}/{p['track_count']}"

    log(f"[tidal] match por ISRC: {hit} encontrados, {miss} sin match, "
        f"{sin_isrc} sin ISRC en el relevamiento")
    return productos


# ============================================================
# Descarga NIVEL A — Tidal FLAC
# ============================================================

def bajar_flac(session, track_id, dest_dir, calidad="LOSSLESS"):
    """Baja un track de Tidal y devuelve (ruta, etiqueta_calidad, formato).

    Verifica el formato REAL de lo que llegó: `extract_flac` sondea el codec y
    devuelve `.m4a` si Tidal sirvió AAC porque no hay máster lossless. En ese
    caso el archivo se etiqueta como lossy, no como apto para entrega.
    """
    from pathlib import Path
    from tiddl.core.utils import get_track_stream_data
    from tiddl.core.utils.ffmpeg import extract_flac
    from tiddl.core.metadata import add_track_metadata

    stream = session.api.get_track_stream(track_id, calidad)
    data, ext = get_track_stream_data(stream)

    tmp = Path(dest_dir) / f"tidal_{track_id}{ext}"
    tmp.write_bytes(data)

    # Sólo intentamos extraer FLAC si Tidal dice que sirvió lossless.
    if getattr(stream, "audioQuality", "") in ("LOSSLESS", "HI_RES_LOSSLESS"):
        try:
            tmp = extract_flac(tmp)
        except Exception:
            pass  # nos quedamos con el contenedor original

    try:
        add_track_metadata(tmp, session.api.get_track(track_id))
    except Exception:
        pass  # sin metadata embebida, pero el audio sirve

    formato = tmp.suffix.lower()
    etiqueta = ETIQUETA_LOSSLESS if formato in FORMATOS_LOSSLESS else ETIQUETA_LOSSY_TIDAL
    return tmp, etiqueta, formato


# ============================================================
# Descarga NIVEL B — YouTube (referencia lossy)
# ============================================================

def _falla(motivo, video_id, log, errores):
    """Registra el motivo del fallo y devuelve la terna vacía."""
    log(f"[yt] {video_id}: {motivo}")
    if errores is not None:
        errores.append(motivo)
    return None, None, None


def bajar_referencia_youtube(video_id, dest_dir, log=print, errores=None):
    """Baja el mejor audio disponible de YouTube, SIN recomprimir.

    Si `errores` es una lista, se le agrega el motivo del fallo. Sin eso el
    usuario ve "sin audio" y no tiene forma de saber si el video se borró, si es
    privado o si hay un problema de red.

    No convertimos a WAV a propósito: la fuente ya es lossy, así que pasarla a
    WAV multiplicaría el peso por ~50 sin recuperar nada. Guardamos el stream
    original (Opus/M4A), que es lo más fiel a lo que YouTube tiene.
    """
    from pathlib import Path
    salida = Path(dest_dir) / f"yt_{video_id}.%(ext)s"
    cmd = ["yt-dlp", "-f", "bestaudio", "--no-playlist", "--quiet", "--no-warnings"]
    js = _runtime_js()
    if js:
        # Sin runtime de JS, YouTube deprecó la extracción y las URLs dan 403.
        cmd += ["--js-runtimes", js]
    cmd += ["-o", str(salida), f"https://www.youtube.com/watch?v={video_id}"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
    except FileNotFoundError:
        return _falla("yt-dlp no está instalado", video_id, log, errores)
    except subprocess.CalledProcessError as e:
        # yt-dlp explica el motivo real en stderr ("Video unavailable", "Private
        # video", "Sign in to confirm your age"...). Es justo lo que el usuario
        # necesita saber para decidir qué hacer con ese track.
        crudo = (e.stderr or "").strip().splitlines()
        motivo = next((l.replace("ERROR:", "").strip() for l in reversed(crudo)
                       if "ERROR" in l.upper()), crudo[-1] if crudo else "falló la descarga")
        return _falla(motivo[:160], video_id, log, errores)
    except subprocess.TimeoutExpired:
        return _falla("tardó demasiado y se canceló", video_id, log, errores)

    for f in Path(dest_dir).glob(f"yt_{video_id}.*"):
        return f, ETIQUETA_YOUTUBE, f.suffix.lower()
    return _falla("yt-dlp terminó pero no dejó ningún archivo", video_id, log, errores)


# ============================================================
# Orquestación
# ============================================================

def fetch_audio(productos, session=None, usar_referencia=True, dest_dir=None,
                calidad="LOSSLESS", log=print):
    """Baja el audio de todos los tracks de los productos seleccionados.

    Estrategia: si hay sesión de Tidal y el track matcheó por ISRC, va por FLAC.
    Si no y `usar_referencia`, cae a YouTube etiquetado como referencia. Cada
    track queda con `audio_path`, `audio_label` y `audio_format`.
    """
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="migrador_audio_")
    os.makedirs(dest_dir, exist_ok=True)
    tracks = [t for p in productos for t in p["tracks"]]

    con_tidal = [t for t in tracks if session and session.conectada and (t.get("tidal") or {}).get("track_id")]
    sin_tidal = [t for t in tracks if t not in con_tidal]

    def _init(t):
        t["audio_path"] = t["audio_label"] = t["audio_format"] = None

    for t in tracks:
        _init(t)

    # --- Nivel A: Tidal ---
    def _tidal(t):
        try:
            ruta, etiqueta, fmt = bajar_flac(session, t["tidal"]["track_id"], dest_dir, calidad)
            t["audio_path"], t["audio_label"], t["audio_format"] = str(ruta), etiqueta, fmt
        except Exception as e:
            t["audio_error"] = str(e)[:160]
        return t

    if con_tidal:
        log(f"[audio] {len(con_tidal)} tracks por Tidal ({calidad})")
        with ThreadPoolExecutor(max_workers=TIDAL_WORKERS) as ex:
            for i, t in enumerate(ex.map(_tidal, con_tidal), 1):
                estado = t.get("audio_format") or f"ERROR {t.get('audio_error', '')}"
                # Sin caracteres fuera de cp1252: la consola de Windows los
                # rechaza y cortaría la migración con UnicodeEncodeError.
                log(f"[audio] tidal {i}/{len(con_tidal)} {t['track'][:40]} -> {estado}")

    # --- Nivel B: YouTube como referencia ---
    fallidos = [t for t in con_tidal if not t.get("audio_path")]
    pendientes = (sin_tidal + fallidos) if usar_referencia else []

    def _yt(t):
        errs = []
        ruta, etiqueta, fmt = bajar_referencia_youtube(
            t.get("video_id"), dest_dir, log=lambda *_: None, errores=errs)
        if ruta:
            t["audio_path"], t["audio_label"], t["audio_format"] = str(ruta), etiqueta, fmt
        elif errs:
            # Antes esto se descartaba y el usuario veía "sin audio" sin motivo.
            t["audio_error"] = errs[-1]
        return t

    if pendientes:
        log(f"[audio] {len(pendientes)} tracks por YouTube (referencia lossy)")
        with ThreadPoolExecutor(max_workers=YT_WORKERS) as ex:
            for i, t in enumerate(ex.map(_yt, pendientes), 1):
                estado = t.get("audio_format") or f"SIN AUDIO ({t.get('audio_error', 'motivo desconocido')})"
                log(f"[audio] yt {i}/{len(pendientes)} {t['track'][:40]} -> {estado}")

    aptos = sum(1 for t in tracks if (t.get("audio_format") or "") in FORMATOS_LOSSLESS)
    ref = sum(1 for t in tracks if t.get("audio_path") and (t.get("audio_format") or "") not in FORMATOS_LOSSLESS)
    log(f"[audio] listos: {aptos} aptos para entrega, {ref} de referencia, "
        f"{len(tracks) - aptos - ref} sin audio")
    return productos, dest_dir

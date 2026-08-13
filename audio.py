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


# ============================================================
# Chequeo de entorno
# ============================================================

def _existe(cmd):
    return shutil.which(cmd) is not None


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
        "tiddl": tiene_tiddl,
        "yt_dlp": tiene_ytdlp,
        # FLAC necesita las dos cosas: tiddl para bajar y ffmpeg para extraer.
        "puede_flac": tiene_tiddl and ffmpeg,
        "puede_referencia": tiene_ytdlp and ffmpeg,
    }


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
            return "pendiente" if e.error == "authorization_pending" else f"error: {e.error}"

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
            from tiddl.core.api import TidalApi, TidalClient
            client = TidalClient(
                token=self._token,
                cache_name=os.path.join(self._cache_dir, "api_cache"),
            )
            self._api = TidalApi(client, self.user_id, self.country_code)
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


def buscar_artista_tidal(session, nombre):
    """Encuentra el artist_id en Tidal. Devuelve (id, nombre) o (None, None)."""
    try:
        res = session.api.get_search(nombre)
    except Exception:
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
    artist_id, nombre_tidal = buscar_artista_tidal(session, artista)
    if not artist_id:
        log(f"[tidal] no encontré al artista '{artista}' en Tidal")
        return {}, None

    log(f"[tidal] artista: {nombre_tidal} (id {artist_id})")
    indice = {}
    try:
        albums = session.api.get_artist_albums(artist_id)
        items = getattr(albums, "items", None) or []
    except Exception as e:
        log(f"[tidal] no pude listar la discografía: {e}")
        return {}, artist_id

    for al in items:
        album_id = getattr(al, "id", None)
        if not album_id:
            continue
        try:
            contenido = session.api.get_album_items(album_id)
            tracks = getattr(contenido, "items", None) or []
        except Exception:
            continue
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

def bajar_referencia_youtube(video_id, dest_dir, log=print):
    """Baja el mejor audio disponible de YouTube, SIN recomprimir.

    No convertimos a WAV a propósito: la fuente ya es lossy, así que pasarla a
    WAV multiplicaría el peso por ~50 sin recuperar nada. Guardamos el stream
    original (Opus/M4A), que es lo más fiel a lo que YouTube tiene.
    """
    from pathlib import Path
    salida = Path(dest_dir) / f"yt_{video_id}.%(ext)s"
    cmd = [
        "yt-dlp", "-f", "bestaudio",
        "--no-playlist", "--quiet", "--no-warnings",
        "-o", str(salida),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
    except FileNotFoundError:
        log("[yt] yt-dlp no está instalado")
        return None, None, None
    except subprocess.CalledProcessError as e:
        log(f"[yt] falló {video_id}: {(e.stderr or '').strip()[:120]}")
        return None, None, None
    except subprocess.TimeoutExpired:
        log(f"[yt] timeout en {video_id}")
        return None, None, None

    for f in Path(dest_dir).glob(f"yt_{video_id}.*"):
        return f, ETIQUETA_YOUTUBE, f.suffix.lower()
    return None, None, None


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
        ruta, etiqueta, fmt = bajar_referencia_youtube(t.get("video_id"), dest_dir, log=lambda *_: None)
        if ruta:
            t["audio_path"], t["audio_label"], t["audio_format"] = str(ruta), etiqueta, fmt
        return t

    if pendientes:
        log(f"[audio] {len(pendientes)} tracks por YouTube (referencia lossy)")
        with ThreadPoolExecutor(max_workers=YT_WORKERS) as ex:
            for i, t in enumerate(ex.map(_yt, pendientes), 1):
                estado = t.get("audio_format") or "sin audio"
                log(f"[audio] yt {i}/{len(pendientes)} {t['track'][:40]} -> {estado}")

    aptos = sum(1 for t in tracks if (t.get("audio_format") or "") in FORMATOS_LOSSLESS)
    ref = sum(1 for t in tracks if t.get("audio_path") and (t.get("audio_format") or "") not in FORMATOS_LOSSLESS)
    log(f"[audio] listos: {aptos} aptos para entrega, {ref} de referencia, "
        f"{len(tracks) - aptos - ref} sin audio")
    return productos, dest_dir

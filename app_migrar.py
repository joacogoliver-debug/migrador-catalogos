"""
app_migrar.py — Migrador de Catálogos (app web Streamlit).

Flujo de 4 pasos:
  1. Pegás el link de YouTube del artista.
  2. Elegís qué productos migrar (a mano, por rango de fechas o por distribuidora).
  3. Elegís qué bajar: planilla / audios / portadas.
  4. Descargás un ZIP organizado por producto.

Claves del servidor (nunca en el código): YOUTUBE_API_KEY, APP_PASSWORD.
El audio en calidad de entrega requiere que cada usuario conecte SU PROPIA
cuenta de Tidal; la app nunca ve su contraseña. Ver MIGRADOR.md.
"""

import os
import tempfile

import streamlit as st

import audio as A
import migrar_core as M
import productos as P
import relevar_core as R

st.set_page_config(page_title="Migrador de Catálogos · Mojo", page_icon="📦", layout="wide")

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], button, input { font-family: 'Inter', sans-serif; }
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { visibility: hidden; }
.block-container { padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1140px; }

.hero {
  background: linear-gradient(135deg, #0F766E 0%, #4F46E5 55%, #7C3AED 100%);
  color: #fff; padding: 30px 34px; border-radius: 20px; margin-bottom: 1.6rem;
  box-shadow: 0 10px 30px rgba(79,70,229,.25);
}
.hero h1 { color:#fff; margin:0; font-size:2.05rem; font-weight:800; letter-spacing:-.5px; }
.hero p  { color:#E0E7FF; margin:.45rem 0 0; font-size:1.02rem; max-width:720px; }

[data-testid="stMetric"] {
  background:#FFFFFF; border:1px solid #ECECF3; border-radius:16px;
  padding:16px 18px; box-shadow:0 1px 3px rgba(16,24,40,.04);
}
[data-testid="stMetricLabel"] { opacity:.65; font-weight:600; font-size:.78rem; text-transform:uppercase; letter-spacing:.4px; }
[data-testid="stMetricValue"] { font-weight:800; }

.stButton>button, .stDownloadButton>button {
  border-radius:11px; font-weight:700; padding:.55rem 1.1rem; border:none;
}
.stDownloadButton>button { background:#16A34A; color:#fff; }

.paso { font-size:.78rem; font-weight:700; letter-spacing:.6px; text-transform:uppercase;
        color:#6366F1; margin-top:1.8rem; }
h3 { font-weight:700; margin-top:.2rem; }

.aviso-lossy {
  background:#FFF7ED; border-left:4px solid #F59E0B; padding:12px 16px;
  border-radius:8px; font-size:.92rem; margin:.6rem 0;
}
.ok-lossless {
  background:#F0FDF4; border-left:4px solid #16A34A; padding:12px 16px;
  border-radius:8px; font-size:.92rem; margin:.6rem 0;
}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


def secret(name, default=""):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


def check_password():
    expected = secret("APP_PASSWORD")
    if not expected:
        return True
    if st.session_state.get("auth_ok"):
        return True
    st.title("📦 Migrador de Catálogos")
    st.caption("Herramienta de Mojo Latam")
    pw = st.text_input("Contraseña", type="password")
    if pw:
        if pw == expected:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")
    return False


def fmt(n):
    try:
        return f"{int(n):,}".replace(",", ".")
    except Exception:
        return str(n)


def paso(n, titulo):
    st.markdown(f'<div class="paso">Paso {n}</div>', unsafe_allow_html=True)
    st.markdown(f"### {titulo}")


# ============================================================
# Paso 1 — relevar
# ============================================================

def paso_1(yt_key):
    paso(1, "Pegá el link del artista en YouTube")
    url = st.text_input(
        "Link del canal / Topic",
        placeholder="https://www.youtube.com/channel/UC...   ó   https://www.youtube.com/@Artista",
        label_visibility="collapsed")

    if st.button("Relevar catálogo", type="primary", disabled=not url):
        box, bar = st.empty(), st.progress(0.0)

        def progress(msg, frac=None):
            box.info(msg)
            if isinstance(frac, (int, float)):
                bar.progress(min(max(frac, 0.0), 1.0))

        try:
            prods, artista, tracks = M.relevar_catalogo(url.strip(), yt_key, progress=progress)
        except R.RelevarError as e:
            bar.empty(); box.empty()
            st.error(str(e))
            return
        except Exception as e:  # noqa: BLE001
            bar.empty(); box.empty()
            st.error(f"Ocurrió un error inesperado: {e}")
            return
        bar.empty(); box.empty()

        st.session_state["prods"] = prods
        st.session_state["artista"] = artista
        st.session_state.pop("zip_path", None)
        st.rerun()


# ============================================================
# Paso 2 — seleccionar productos
# ============================================================

def paso_2(prods, artista):
    paso(2, f"Elegí qué productos migrar — {artista}")

    res = P.summarize(prods)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Productos", fmt(res["products"]))
    c2.metric("Tracks", fmt(res["tracks"]))
    c3.metric("Con UPC", f"{res['with_upc']}/{res['products']}")
    c4.metric("Con ISRC", f"{res['with_isrc']}/{res['tracks']}")

    opciones = M.opciones_de_filtro(prods)
    modo = st.radio(
        "Cómo querés elegir",
        ["Uno por uno", "Por rango de fechas", "Por distribuidora"],
        horizontal=True)

    seleccion = prods

    if modo == "Por rango de fechas":
        lo, hi = opciones["año_min"], opciones["año_max"]
        if lo is None:
            st.warning("Ningún producto tiene año de lanzamiento: usá otro filtro.")
            seleccion = []
        elif lo == hi:
            st.info(f"Todo el catálogo es del año {lo}.")
            seleccion = P.filter_products(prods, year_from=lo, year_to=hi)
        else:
            desde, hasta = st.slider("Años de lanzamiento", lo, hi, (lo, hi))
            seleccion = P.filter_products(prods, year_from=desde, year_to=hasta)

    elif modo == "Por distribuidora":
        nombres = [f"{d['name']} ({d['count']})" for d in opciones["distribuidoras"]]
        elegidas = st.multiselect("Distribuidoras", nombres, default=nombres)
        crudas = [opciones["distribuidoras"][nombres.index(e)]["name"] for e in elegidas]
        seleccion = P.filter_products(prods, distributors=crudas) if crudas else []

    else:
        st.caption("Destildá los que no quieras migrar.")
        filas = [{
            "Migrar": True,
            "Producto": p["title"],
            "Tipo": p["kind"],
            "Año": p.get("release_year") or "",
            "Tracks": p["track_count"],
            "UPC": p.get("upc") or "—",
            "Distribuidora": p.get("distributor") or "—",
            "_id": p["product_id"],
        } for p in prods]
        editado = st.data_editor(
            filas, hide_index=True, width="stretch", height=380,
            column_config={
                "Migrar": st.column_config.CheckboxColumn(required=True),
                "_id": None,
            },
            disabled=["Producto", "Tipo", "Año", "Tracks", "UPC", "Distribuidora"])
        ids = [f["_id"] for f in editado if f["Migrar"]]
        seleccion = P.filter_products(prods, ids=ids)

    if seleccion:
        s = P.summarize(seleccion)
        st.success(
            f"Seleccionados: **{s['products']} productos** · {s['tracks']} tracks "
            f"({s['albums']} álbumes, {s['eps']} EPs, {s['singles']} singles)")
    else:
        st.warning("No hay productos seleccionados.")

    st.session_state["seleccion"] = seleccion
    return seleccion


# ============================================================
# Paso 3 — qué bajar (+ conectar Tidal)
# ============================================================

def _conectar_tidal(entorno):
    """Flujo device-code: mostramos un código, el usuario se autentica en Tidal.
    Su contraseña nunca pasa por esta app."""
    ses = st.session_state.get("tidal")

    if ses and ses.conectada:
        c1, c2 = st.columns([3, 1])
        c1.markdown('<div class="ok-lossless">✅ <b>Cuenta de Tidal conectada.</b> '
                    'El audio va a bajar en FLAC lossless, apto para entrega.</div>',
                    unsafe_allow_html=True)
        if c2.button("Desconectar"):
            ses.close()
            st.session_state.pop("tidal", None)
            st.session_state.pop("tidal_login", None)
            st.rerun()
        return ses

    if not entorno["tiddl"]:
        st.info("El módulo de Tidal no está instalado en el servidor "
                "(`pip install tiddl`). Sin él, el audio sólo puede ser de referencia.")
        return None
    if not entorno["ffmpeg"]:
        st.info("Falta **ffmpeg** en el servidor, que es necesario para extraer el "
                "FLAC. Sin él, el audio sólo puede ser de referencia.")
        return None

    login = st.session_state.get("tidal_login")

    if not login:
        st.caption("Conectá tu cuenta de Tidal para bajar el audio en calidad de entrega. "
                   "Te vamos a mandar al sitio de Tidal: tu contraseña nunca pasa por acá.")
        if st.button("Conectar mi cuenta de Tidal"):
            ses = A.TidalSession()
            try:
                st.session_state["tidal_login"] = ses.iniciar_login()
                st.session_state["tidal"] = ses
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"No pude iniciar el login de Tidal: {e}")
        return None

    st.markdown(
        f'Abrí **[{login["url"]}]({login["url"]})** y confirmá el acceso. '
        f'Si te pide un código, es: `{login["user_code"]}`')
    c1, c2 = st.columns([1, 3])
    if c1.button("Ya confirmé", type="primary"):
        estado = st.session_state["tidal"].poll_login(login["device_code"])
        if estado == "ok":
            st.session_state.pop("tidal_login", None)
            st.rerun()
        elif estado == "pendiente":
            st.warning("Todavía no me llegó la confirmación. Completá el acceso en "
                       "Tidal y volvé a apretar.")
        else:
            st.error(f"Falló la conexión ({estado}). Probá de nuevo.")
    if c2.button("Cancelar"):
        st.session_state["tidal"].close()
        st.session_state.pop("tidal", None)
        st.session_state.pop("tidal_login", None)
        st.rerun()
    return None


def paso_3(seleccion, artista):
    paso(3, "Elegí qué querés descargar")
    entorno = A.verificar_entorno()

    c1, c2, c3 = st.columns(3)
    quiere_planilla = c1.checkbox("📄 Planilla con datos y códigos", value=True)
    quiere_portadas = c2.checkbox("🖼️ Portadas", value=True,
                                  help="Hasta 3000x3000 desde Apple Music.")
    quiere_audio = c3.checkbox("🎵 Audios", value=False)

    ses = None
    if quiere_audio:
        st.markdown("---")
        ses = _conectar_tidal(entorno)
        con_tidal = bool(ses and ses.conectada)

        usar_ref = st.checkbox(
            "Bajar audio de referencia (YouTube) cuando no haya FLAC disponible",
            value=True,
            help="Audio ya comprimido por YouTube. Sirve para inventario y "
                 "verificación, no para entregar.")

        if not con_tidal:
            if usar_ref and entorno["puede_referencia"]:
                st.markdown(
                    '<div class="aviso-lossy">⚠️ <b>Sin cuenta de Tidal conectada.</b> '
                    'Todo el audio va a ser <b>referencia lossy de YouTube</b>: sirve para '
                    'inventario o verificación, pero <b>no es apto para entregar</b> a una '
                    'distribuidora. Para una entrega real hace falta el máster original.</div>',
                    unsafe_allow_html=True)
            elif not entorno["puede_referencia"]:
                st.error("No hay forma de bajar audio en este servidor: falta `yt-dlp` "
                         "o `ffmpeg`. Podés seguir con planilla y portadas.")
        return quiere_planilla, quiere_portadas, quiere_audio, usar_ref, ses, entorno

    return quiere_planilla, quiere_portadas, quiere_audio, False, None, entorno


# ============================================================
# Paso 4 — generar y descargar
# ============================================================

def paso_4(seleccion, artista, quiere_planilla, quiere_portadas, quiere_audio,
           usar_ref, ses, entorno):
    paso(4, "Generá el paquete")

    if not (quiere_planilla or quiere_portadas or quiere_audio):
        st.warning("Elegí al menos una cosa para descargar.")
        return

    if st.button("Generar paquete", type="primary"):
        box = st.empty()
        registro = []

        def log(msg):
            registro.append(str(msg))
            box.info(str(msg))

        dir_audio = None
        with st.spinner("Preparando el paquete…"):
            try:
                sel, dir_audio, ent = M.preparar(
                    seleccion, artista,
                    quiere_planilla=quiere_planilla, quiere_audio=quiere_audio,
                    quiere_portadas=quiere_portadas,
                    tidal_session=ses, usar_referencia=usar_ref, log=log)
                out = os.path.join(tempfile.mkdtemp(prefix="migrador_zip_"),
                                   f"{R.slugify(artista)}-migracion.zip")
                ruta, tam = M.empaquetar(
                    sel, artista, out_path=out, entorno=ent,
                    con_tidal=bool(ses and ses.conectada),
                    incluir_planilla=quiere_planilla, incluir_audio=quiere_audio,
                    incluir_portadas=quiere_portadas, log=log)
            except Exception as e:  # noqa: BLE001
                box.empty()
                st.error(f"Falló al armar el paquete: {e}")
                return
            finally:
                # Los audios ya están dentro del ZIP: el temporal no hace falta.
                M.limpiar(dir_audio)

        box.empty()
        st.session_state["zip_path"] = ruta
        st.session_state["zip_bytes"] = tam
        st.session_state["zip_log"] = registro
        st.session_state["zip_sel"] = sel
        st.rerun()

    if st.session_state.get("zip_path"):
        _render_descarga(artista)


def _render_descarga(artista):
    ruta = st.session_state["zip_path"]
    tam = st.session_state.get("zip_bytes", 0)
    sel = st.session_state.get("zip_sel", [])

    if not os.path.exists(ruta):
        st.info("El paquete anterior ya se limpió. Generalo de nuevo.")
        st.session_state.pop("zip_path", None)
        return

    tracks = [t for p in sel for t in p["tracks"]]
    aptos = [t for t in tracks if (t.get("audio_format") or "") in A.FORMATOS_LOSSLESS]
    ref = [t for t in tracks if t.get("audio_path") and t not in aptos]

    st.success(f"✅ Paquete listo — {len(sel)} productos · {tam / 1e6:.1f} MB")

    c1, c2, c3 = st.columns(3)
    c1.metric("Portadas", f"{sum(1 for p in sel if p.get('cover_bytes'))}/{len(sel)}")
    c2.metric("Audio apto entrega", fmt(len(aptos)))
    c3.metric("Sólo referencia", fmt(len(ref)))

    if ref:
        st.markdown(
            f'<div class="aviso-lossy">⚠️ <b>{len(ref)} tracks quedaron sólo en calidad '
            'de referencia</b> y no son aptos para entregar. Están marcados '
            '<code>[REFERENCIA-LOSSY]</code> en el nombre del archivo y resaltados en la '
            'planilla. El reporte dentro del ZIP los lista uno por uno.</div>',
            unsafe_allow_html=True)

    with open(ruta, "rb") as f:
        st.download_button(
            "⬇️ Descargar ZIP", data=f, file_name=os.path.basename(ruta),
            mime="application/zip", type="primary")

    with st.expander("Ver el detalle del proceso"):
        st.code("\n".join(st.session_state.get("zip_log", [])), language=None)


# ============================================================

def main():
    if not check_password():
        return

    yt_key = secret("YOUTUBE_API_KEY")

    st.markdown(
        '<div class="hero"><h1>📦 Migrador de Catálogos</h1>'
        '<p>Pegá el link de YouTube de un artista, elegí qué productos migrar y '
        'descargá una carpeta organizada con la planilla de códigos, los audios y '
        'las portadas — lista para entregar a la distribuidora nueva.</p></div>',
        unsafe_allow_html=True)

    if not yt_key:
        st.error("⚠️ Falta configurar `YOUTUBE_API_KEY` en el servidor. Avisá al administrador.")
        return

    paso_1(yt_key)

    prods = st.session_state.get("prods")
    if not prods:
        return

    artista = st.session_state.get("artista", "")
    seleccion = paso_2(prods, artista)
    if not seleccion:
        return

    st.markdown("---")
    opts = paso_3(seleccion, artista)

    st.markdown("---")
    paso_4(seleccion, artista, *opts)


main()

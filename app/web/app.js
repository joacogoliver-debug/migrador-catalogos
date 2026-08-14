/* ============================================================
   Migrador de Catálogos — frontend.

   Vanilla JS a propósito: sin React, sin Babel en el navegador y sin CDN. La app
   se empaqueta como ejecutable, así que no puede depender de una descarga en
   tiempo de ejecución, y sin build step el binario se arma con sólo copiar
   archivos.

   Patrón: un objeto de estado (S), una función render() que dibuja según el
   paso, y eventos por delegación. Simple y suficiente para cuatro pantallas.
   ============================================================ */

'use strict';

const S = {
  paso: 1,
  config: null,
  catalogo: null,
  seleccion: new Set(),
  expandidos: new Set(),
  filtro: { modo: 'manual', anioDesde: null, anioHasta: null, distribs: new Set(), texto: '' },
  opciones: { planilla: true, portadas: true, audio: false },
  job: null,
  resultado: null,
  error: '',
  tidal: null,
  ocupado: false,
};

const $ = (sel) => document.querySelector(sel);
const pantalla = () => $('#pantalla');

/* ------------------------------------------------------------ utilidades */

/** Escapa para insertar en HTML. Los títulos de YouTube traen de todo. */
function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function num(n) {
  return (Number(n) || 0).toLocaleString('es-AR');
}

function pesoLegible(bytes) {
  const b = Number(bytes) || 0;
  if (b >= 1e9) return (b / 1e9).toFixed(2) + ' GB';
  if (b >= 1e6) return (b / 1e6).toFixed(1) + ' MB';
  if (b >= 1e3) return Math.round(b / 1e3) + ' KB';
  return b + ' B';
}

async function api(ruta, cuerpo) {
  const opciones = cuerpo === undefined
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo) };
  const r = await fetch(ruta, opciones);
  let datos = {};
  try { datos = await r.json(); } catch (_) { /* respuesta sin cuerpo */ }
  if (!r.ok) throw new Error(datos.error || `Error ${r.status}`);
  return datos;
}

/** Espera a que termine un trabajo del backend, mostrando el avance. */
async function esperarJob(job, alAvanzar) {
  let id = job.id;
  for (;;) {
    await new Promise((res) => setTimeout(res, 400));
    let j;
    try {
      j = await api(`/api/job/${id}`);
    } catch (e) {
      throw new Error('Se perdió la conexión con la app. ¿Se cerró la ventana del servidor?');
    }
    S.job = j;
    if (alAvanzar) alAvanzar(j);
    if (j.estado === 'listo') return j.resultado;
    if (j.estado === 'error') throw new Error(j.error || 'El proceso falló.');
    if (j.estado === 'cancelado') throw new Error('CANCELADO');
  }
}

/* ------------------------------------------------------------ stepper */

const PASOS = ['Pegá el link', 'Elegí productos', 'Elegí qué bajar', 'Descargá'];

function renderStepper() {
  const partes = [];
  PASOS.forEach((nombre, i) => {
    const n = i + 1;
    const clase = S.paso === n ? 'activo' : (S.paso > n ? 'hecho' : '');
    partes.push(
      `<div class="step ${clase}"><span class="step-num">${S.paso > n ? '✓' : n}</span><span>${esc(nombre)}</span></div>`
    );
    if (n < PASOS.length) partes.push('<div class="step-linea"></div>');
  });
  $('#stepper').innerHTML = partes.join('');
}

/* ------------------------------------------------------------ productos filtrados */

function productosFiltrados() {
  if (!S.catalogo) return [];
  let ps = S.catalogo.productos;
  const f = S.filtro;

  if (f.modo === 'fechas' && f.anioDesde !== null) {
    ps = ps.filter((p) => p.anio && p.anio >= f.anioDesde && p.anio <= f.anioHasta);
  } else if (f.modo === 'distribuidora' && f.distribs.size) {
    ps = ps.filter((p) => f.distribs.has(p.distribuidora));
  }

  if (f.texto.trim()) {
    const q = f.texto.trim().toLowerCase();
    ps = ps.filter((p) =>
      p.titulo.toLowerCase().includes(q) ||
      (p.upc || '').includes(q) ||
      (p.distribuidora || '').toLowerCase().includes(q) ||
      p.detalle.some((t) => t.titulo.toLowerCase().includes(q) || (t.isrc || '').toLowerCase().includes(q))
    );
  }
  return ps;
}

function seleccionados() {
  return productosFiltrados().filter((p) => S.seleccion.has(p.id));
}

/* ------------------------------------------------------------ render principal */

/* Redibujar reemplaza el DOM, así que el elemento que tenía el foco desaparece.
   Sin esto, alguien que navegue con teclado pierde su lugar cada vez que tilda
   un filtro. Guardamos cómo identificar al elemento enfocado y lo recuperamos. */

const ATRIBUTOS_FOCO = ['data-distrib', 'data-prod', 'data-opcion-check', 'data-modo', 'data-accion'];

function tomarFoco() {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const marca = { id: el.id || null, attr: null, valor: null, inicio: null, fin: null };
  if (!marca.id) {
    for (const a of ATRIBUTOS_FOCO) {
      if (el.hasAttribute(a)) { marca.attr = a; marca.valor = el.getAttribute(a); break; }
    }
  }
  if (!marca.id && !marca.attr) return null;
  // En campos de texto conservamos también la posición del cursor.
  if (typeof el.selectionStart === 'number') {
    try { marca.inicio = el.selectionStart; marca.fin = el.selectionEnd; } catch (_) {}
  }
  return marca;
}

function devolverFoco(marca) {
  if (!marca) return;
  let el = null;
  if (marca.id) {
    el = document.getElementById(marca.id);
  } else {
    // Comparamos el valor en JS en vez de armar un selector: los nombres de
    // distribuidora pueden traer comillas y romperían el selector.
    el = [...document.querySelectorAll(`[${marca.attr}]`)]
      .find((n) => n.getAttribute(marca.attr) === marca.valor) || null;
  }
  if (!el) return;
  try {
    el.focus({ preventScroll: true });
    if (marca.inicio !== null && typeof el.setSelectionRange === 'function') {
      el.setSelectionRange(marca.inicio, marca.fin);
    }
  } catch (_) { /* el elemento ya no acepta foco */ }
}

function render() {
  const foco = tomarFoco();

  renderStepper();
  $('#version').textContent = S.config ? 'v' + S.config.version : '';

  if (!S.config) { pantalla().innerHTML = vistaCargando('Iniciando…'); return; }
  if (!S.config.tiene_clave) { pantalla().innerHTML = vistaSetup(); devolverFoco(foco); return; }

  if (S.paso === 1) pantalla().innerHTML = vistaPaso1();
  else if (S.paso === 2) pantalla().innerHTML = vistaPaso2();
  else if (S.paso === 3) pantalla().innerHTML = vistaPaso3();
  else pantalla().innerHTML = vistaPaso4();

  // El estado "indeterminado" de un checkbox no existe en HTML: es una
  // propiedad que hay que setear por JS después de dibujar.
  const todos = $('#check-todos');
  if (todos && todos.dataset.indeterminado) todos.indeterminate = true;

  devolverFoco(foco);
}

function vistaCargando(texto) {
  return `<div class="card"><div class="row"><span class="spinner"></span><span>${esc(texto)}</span></div></div>`;
}

function alerta(tipo, icono, html) {
  return `<div class="alerta alerta-${tipo}"><span class="alerta-icono">${icono}</span><div>${html}</div></div>`;
}

/* ------------------------------------------------------------ setup inicial */

function vistaSetup() {
  return `
  <div class="card card-hero fade">
    <div class="card-head">
      <div class="eyebrow">Configuración inicial</div>
      <h1>Conectá tu clave de YouTube</h1>
      <p>Se pide una sola vez. Queda guardada en tu computadora y no se comparte con nadie.</p>
    </div>

    ${alerta('', 'ℹ️', `
      <strong>Cómo conseguirla, en 3 pasos:</strong>
      <ol style="margin:8px 0 0 18px;padding:0;line-height:1.75">
        <li>Entrá a <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noopener">Google Cloud Console → Credenciales</a> y creá un proyecto.</li>
        <li>Activá <em>YouTube Data API v3</em> en la biblioteca de APIs.</li>
        <li>Creá una <em>clave de API</em> y pegala acá abajo.</li>
      </ol>
      <p class="small muted" style="margin-top:8px">Es gratis. El cupo diario alcanza para unos 500 catálogos.</p>`)}

    <div class="field" style="margin-top:20px">
      <label for="clave">Clave de la API de YouTube</label>
      <input class="input input-lg mono" id="clave" type="password" placeholder="AIza…" autocomplete="off" spellcheck="false" />
      <span class="hint">La verificamos con una consulta de prueba antes de guardarla.</span>
    </div>

    <div id="setup-error"></div>

    <div class="row" style="margin-top:20px">
      <button class="btn btn-primary btn-lg" data-accion="guardar-clave">Verificar y guardar</button>
    </div>
  </div>`;
}

/* ------------------------------------------------------------ paso 1 */

function vistaPaso1() {
  const corriendo = S.ocupado;
  return `
  <div class="card card-hero fade">
    <div class="card-head">
      <div class="eyebrow">Paso 1 de 4</div>
      <h1>¿Qué catálogo querés migrar?</h1>
      <p>Pegá el link del canal de YouTube del artista. Sirve el canal Topic, el canal oficial o un <span class="mono">@handle</span>.</p>
    </div>

    <div class="field">
      <input class="input input-lg" id="url" type="url" spellcheck="false"
             placeholder="https://www.youtube.com/@Artista"
             ${corriendo ? 'disabled' : ''} />
      <span class="hint">Ejemplo: <span class="mono">https://www.youtube.com/channel/UC…</span></span>
    </div>

    <label class="check" style="margin-top:18px">
      <input type="checkbox" id="con-codigos" checked ${corriendo ? 'disabled' : ''} />
      <span class="check-texto">
        <strong>Buscar códigos ISRC y UPC</strong>
        <span class="sub">Los busca en Deezer, sin clave ni costo. Tarda un poco más pero son los códigos que la distribuidora nueva necesita.</span>
      </span>
    </label>

    ${S.error ? alerta('danger', '⛔', `<strong>No se pudo relevar.</strong><br>${esc(S.error)}`) : ''}

    ${corriendo ? bloqueProgreso() : `
      <div class="row" style="margin-top:22px">
        <button class="btn btn-primary btn-lg" data-accion="relevar">Relevar catálogo</button>
      </div>`}
  </div>`;
}

function bloqueProgreso(conCancelar = true) {
  const j = S.job || { progreso: 0, mensaje: 'Preparando…', log: [] };
  const indef = !j.progreso;
  const log = (j.log || []).slice(-60).join('\n');
  return `
  <div style="margin-top:22px">
    <div class="row" style="margin-bottom:10px">
      <span class="spinner"></span>
      <strong id="progreso-mensaje">${esc(j.mensaje || 'Trabajando…')}</strong>
      <span class="muted small">${j.progreso ? Math.round(j.progreso * 100) + '%' : ''}</span>
      ${conCancelar ? '<button class="btn btn-ghost btn-sm" style="margin-left:auto" data-accion="cancelar">Cancelar</button>' : ''}
    </div>
    <div class="barra ${indef ? 'barra-indef' : ''}">
      <div class="barra-fill" style="width:${Math.round((j.progreso || 0) * 100)}%"></div>
    </div>
    ${log ? `<div class="log" id="log">${esc(log)}</div>` : ''}
  </div>`;
}

/* ------------------------------------------------------------ paso 2 */

function vistaPaso2() {
  const c = S.catalogo;
  const ps = productosFiltrados();
  const sel = seleccionados();
  const r = c.resumen;

  return `
  <div class="fade">
    <div class="card">
      <div class="card-head row row-wrap">
        <div class="grow">
          <div class="eyebrow">Paso 2 de 4 · Catálogo relevado</div>
          <h1>${esc(c.artista)}</h1>
        </div>
        <button class="btn btn-ghost" data-accion="volver-1">← Otro artista</button>
      </div>

      <div class="kpis">
        <div class="kpi"><div class="kpi-label">Productos</div><div class="kpi-valor">${num(r.products)}</div></div>
        <div class="kpi"><div class="kpi-label">Tracks</div><div class="kpi-valor">${num(r.tracks)}</div></div>
        <div class="kpi"><div class="kpi-label">Con UPC</div><div class="kpi-valor">${r.with_upc}<span class="kpi-sub"> / ${r.products}</span></div></div>
        <div class="kpi"><div class="kpi-label">Con ISRC</div><div class="kpi-valor">${r.with_isrc}<span class="kpi-sub"> / ${r.tracks}</span></div></div>
        <div class="kpi"><div class="kpi-label">Reproducciones</div><div class="kpi-valor">${num(r.views)}</div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <h2>Elegí qué productos migrar</h2>
        <p>Podés marcarlos a mano, o filtrar por fecha o por distribuidora y llevarte todo lo que quede.</p>
      </div>

      <div class="row row-wrap" style="margin-bottom:16px">
        <div class="segmented" role="tablist">
          <button class="${S.filtro.modo === 'manual' ? 'activo' : ''}" data-modo="manual">Uno por uno</button>
          <button class="${S.filtro.modo === 'fechas' ? 'activo' : ''}" data-modo="fechas">Por fecha</button>
          <button class="${S.filtro.modo === 'distribuidora' ? 'activo' : ''}" data-modo="distribuidora">Por distribuidora</button>
        </div>
        <div class="grow" style="min-width:180px">
          <input class="input" id="buscar" type="search" placeholder="Buscar por título, ISRC, UPC…"
                 value="${esc(S.filtro.texto)}" />
        </div>
      </div>

      ${panelFiltro()}

      ${ps.length === 0 ? `
        <div class="vacio">
          <div class="vacio-icono">🔍</div>
          <strong>Ningún producto coincide con el filtro.</strong>
          <p class="small">Probá ampliar el rango o limpiar la búsqueda.</p>
        </div>` : tablaProductos(ps)}
    </div>

    <div class="barra-accion">
      <div class="resumen">
        <strong>${sel.length} de ${ps.length}</strong> productos elegidos
        · ${sel.reduce((a, p) => a + p.tracks, 0)} tracks
      </div>
      <div class="acciones">
        <button class="btn btn-secondary" data-accion="sel-todo">Marcar todo</button>
        <button class="btn btn-secondary" data-accion="sel-nada">Desmarcar</button>
        <button class="btn btn-primary" data-accion="ir-3" ${sel.length ? '' : 'disabled'}>
          Continuar →
        </button>
      </div>
    </div>
  </div>`;
}

function panelFiltro() {
  const f = S.filtro;
  const c = S.catalogo;

  if (f.modo === 'fechas') {
    const lo = c.filtros.anio_min, hi = c.filtros.anio_max;
    if (lo === null) {
      return alerta('warn', '⚠️', 'Ningún producto tiene año de lanzamiento declarado. Usá otro filtro.');
    }
    return `
    <div class="card" style="background:var(--surface-subtle);box-shadow:none;margin-bottom:16px">
      <div class="row row-wrap">
        <div class="field" style="max-width:150px">
          <label for="anio-desde">Desde el año</label>
          <input class="input" id="anio-desde" type="number" min="${lo}" max="${hi}" value="${f.anioDesde ?? lo}" />
        </div>
        <div class="field" style="max-width:150px">
          <label for="anio-hasta">Hasta el año</label>
          <input class="input" id="anio-hasta" type="number" min="${lo}" max="${hi}" value="${f.anioHasta ?? hi}" />
        </div>
        <p class="small muted" style="align-self:flex-end;padding-bottom:12px">
          El catálogo va de ${lo} a ${hi}. Los productos sin año quedan afuera.
        </p>
      </div>
    </div>`;
  }

  if (f.modo === 'distribuidora') {
    const items = c.filtros.distribuidoras.map((d) => `
      <label class="check" style="margin-right:20px;margin-bottom:8px">
        <input type="checkbox" data-distrib="${esc(d.name)}" ${f.distribs.has(d.name) ? 'checked' : ''} />
        <span class="check-texto"><strong>${esc(d.name)}</strong><span class="sub">${d.count} producto${d.count === 1 ? '' : 's'}</span></span>
      </label>`).join('');
    return `
    <div class="card" style="background:var(--surface-subtle);box-shadow:none;margin-bottom:16px">
      <div class="row row-wrap">${items}</div>
    </div>`;
  }
  return '';
}

function tablaProductos(ps) {
  const todos = ps.length > 0 && ps.every((p) => S.seleccion.has(p.id));
  const algunos = ps.some((p) => S.seleccion.has(p.id));

  const filas = ps.map((p) => {
    const elegido = S.seleccion.has(p.id);
    const abierto = S.expandidos.has(p.id);
    const avisos = [];
    if (!p.upc) avisos.push('<span class="badge badge-danger">sin UPC</span>');
    if (p.con_isrc < p.tracks) avisos.push(`<span class="badge badge-danger">ISRC ${p.con_isrc}/${p.tracks}</span>`);
    if (p.orden_estimado) avisos.push('<span class="badge">orden estimado</span>');

    const detalle = abierto ? `
      <tr class="fila-detalle"><td colspan="7"><div class="detalle-inner">
        <table class="sub">
          <thead><tr><th>#</th><th>Track</th><th>ISRC</th><th>Duración</th><th class="td-num">Reproducciones</th></tr></thead>
          <tbody>${p.detalle.map((t) => `
            <tr>
              <td class="muted">${esc(t.n)}</td>
              <td>${esc(t.titulo)}</td>
              <td class="mono">${t.isrc ? esc(t.isrc) : '<span class="muted">—</span>'}</td>
              <td class="muted">${esc(t.duracion)}</td>
              <td class="td-num">${num(t.views)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div></td></tr>` : '';

    return `
    <tr class="${elegido ? 'elegida' : ''}" data-fila="${esc(p.id)}">
      <td class="td-check">
        <label class="check"><input type="checkbox" data-prod="${esc(p.id)}" ${elegido ? 'checked' : ''} /></label>
      </td>
      <td>
        <button class="btn-expandir" data-expandir="${esc(p.id)}" title="Ver tracks"
                aria-label="Ver tracks">${abierto ? '▾' : '▸'}</button>
      </td>
      <td>
        <div class="celda-titulo">${esc(p.titulo)}</div>
        <div class="celda-sub">${p.tracks} track${p.tracks === 1 ? '' : 's'}${p.sello ? ' · ' + esc(p.sello) : ''}</div>
      </td>
      <td><span class="badge ${p.tipo === 'album' ? 'badge-dark' : 'badge-accent'}">${esc(p.tipo)}</span></td>
      <td class="nowrap">${esc(p.anio) || '<span class="muted">s/f</span>'}</td>
      <td class="mono small">${p.upc ? esc(p.upc) : '<span class="muted">—</span>'}</td>
      <td>${avisos.join(' ') || '<span class="badge badge-ok">completo</span>'}</td>
    </tr>${detalle}`;
  }).join('');

  return `
  <div class="tabla-wrap" id="tabla-wrap">
    <table class="tabla">
      <thead><tr>
        <th class="td-check"><label class="check"><input type="checkbox" id="check-todos"
          ${todos ? 'checked' : ''} ${!todos && algunos ? 'data-indeterminado="1"' : ''} /></label></th>
        <th></th>
        <th>Producto</th><th>Tipo</th><th>Año</th><th>UPC</th><th>Pendientes</th>
      </tr></thead>
      <tbody>${filas}</tbody>
    </table>
  </div>`;
}

/* ------------------------------------------------------------ paso 3 */

function vistaPaso3() {
  const sel = seleccionados();
  const o = S.opciones;
  const cfg = S.config;
  const audioOn = cfg.audio_habilitado;
  const puedeAudio = audioOn && (cfg.entorno.puede_flac || cfg.entorno.puede_referencia);
  const tidalOk = cfg.tidal_conectada;

  return `
  <div class="fade">
    <div class="card">
      <div class="card-head">
        <div class="eyebrow">Paso 3 de 4</div>
        <h1>¿Qué querés descargar?</h1>
        <p>${sel.length} producto${sel.length === 1 ? '' : 's'} elegido${sel.length === 1 ? '' : 's'}
           · ${sel.reduce((a, p) => a + p.tracks, 0)} tracks</p>
      </div>

      <div class="opciones">
        <div class="opcion ${o.planilla ? 'elegida' : ''}" data-opcion="planilla">
          <div class="opcion-icono">📄</div>
          <label class="check">
            <input type="checkbox" ${o.planilla ? 'checked' : ''} data-opcion-check="planilla" />
            <span class="check-texto"><strong>Planilla y validación</strong>
              <span class="sub">Excel con los datos y códigos, hoja de ingesta CSV para la distribuidora, y el informe de validación previa.</span>
            </span>
          </label>
        </div>

        <div class="opcion ${o.portadas ? 'elegida' : ''}" data-opcion="portadas">
          <div class="opcion-icono">🖼️</div>
          <label class="check">
            <input type="checkbox" ${o.portadas ? 'checked' : ''} data-opcion-check="portadas" />
            <span class="check-texto"><strong>Portadas</strong>
              <span class="sub">La resolución más alta que tenga Apple Music. Te avisamos si queda por debajo del mínimo de ingesta.</span>
            </span>
          </label>
        </div>

        ${audioOn ? `
        <div class="opcion ${o.audio ? 'elegida' : ''} ${puedeAudio ? '' : 'deshabilitada'}" data-opcion="audio">
          <div class="opcion-icono">🎵</div>
          <label class="check">
            <input type="checkbox" ${o.audio ? 'checked' : ''} ${puedeAudio ? '' : 'disabled'} data-opcion-check="audio" />
            <span class="check-texto"><strong>Audios</strong>
              <span class="sub">${puedeAudio
                ? 'FLAC lossless si conectás tu cuenta de Tidal; si no, referencia de YouTube.'
                : 'No disponible: falta ffmpeg o las dependencias de audio.'}</span>
            </span>
          </label>
        </div>` : ''}
      </div>

      ${o.audio && audioOn ? bloqueTidal(tidalOk) : ''}

      ${S.error ? alerta('danger', '⛔', esc(S.error)) : ''}
    </div>

    <div class="barra-accion">
      <div class="resumen">Se va a generar un ZIP con una carpeta por producto.</div>
      <div class="acciones">
        <button class="btn btn-secondary" data-accion="volver-2">← Volver</button>
        <button class="btn btn-primary" data-accion="generar"
          ${(o.planilla || o.portadas || o.audio) ? '' : 'disabled'}>Generar paquete</button>
      </div>
    </div>
  </div>`;
}

function bloqueTidal(conectada) {
  if (conectada) {
    return `<div style="margin-top:18px">${alerta('ok', '✅', `
      <strong>Cuenta de Tidal conectada.</strong> El audio va a bajar en FLAC lossless, apto para entrega.
      <button class="btn btn-ghost btn-sm" data-accion="tidal-salir" style="margin-left:8px">Desconectar</button>`)}</div>`;
  }
  if (S.tidal) {
    return `<div style="margin-top:18px">${alerta('', '🔗', `
      <strong>Conectá tu cuenta en el sitio de Tidal.</strong><br>
      Abrí <a href="${esc(S.tidal.url)}" target="_blank" rel="noopener">${esc(S.tidal.url)}</a>
      ${S.tidal.codigo ? `y usá el código <span class="mono"><strong>${esc(S.tidal.codigo)}</strong></span>` : ''}.
      <div class="row" style="margin-top:10px">
        <button class="btn btn-dark btn-sm" data-accion="tidal-confirmar">Ya confirmé</button>
        <button class="btn btn-ghost btn-sm" data-accion="tidal-salir">Cancelar</button>
      </div>
      ${S.tidal.aviso ? `<p class="small" style="margin-top:8px">${esc(S.tidal.aviso)}</p>` : ''}`)}</div>`;
  }
  return `<div style="margin-top:18px">${alerta('warn', '⚠️', `
    <strong>Sin cuenta de Tidal, el audio va a ser de referencia.</strong>
    El audio de YouTube ya viene comprimido: sirve para inventario o verificación, pero
    <strong>no es apto para entregar</strong> a una distribuidora.
    <div class="row" style="margin-top:10px">
      <button class="btn btn-dark btn-sm" data-accion="tidal-iniciar">Conectar mi cuenta de Tidal</button>
    </div>
    <p class="small muted" style="margin-top:8px">Tu contraseña nunca pasa por esta app: te autenticás en el sitio de Tidal.</p>`)}</div>`;
}

/* ------------------------------------------------------------ paso 4 */

function vistaPaso4() {
  if (S.ocupado) {
    return `<div class="card fade">
      <div class="card-head">
        <div class="eyebrow">Paso 4 de 4</div>
        <h1>Armando el paquete</h1>
        <p>Podés dejar la ventana abierta; te avisamos cuando esté.</p>
      </div>
      ${bloqueProgreso()}
    </div>`;
  }

  if (S.error) {
    return `<div class="card fade">
      <div class="card-head"><div class="eyebrow">Paso 4 de 4</div><h1>No se pudo generar</h1></div>
      ${alerta('danger', '⛔', esc(S.error))}
      <div class="row" style="margin-top:18px">
        <button class="btn btn-secondary" data-accion="volver-3">← Volver</button>
        <button class="btn btn-primary" data-accion="generar">Reintentar</button>
      </div>
    </div>`;
  }

  const r = S.resultado;
  if (!r) return vistaCargando('Preparando…');
  const v = r.validacion || { apto: true, resumen: { errores: 0, avisos: 0 }, hallazgos: [] };

  return `
  <div class="fade">
    <div class="card">
      <div class="card-head">
        <div class="eyebrow">Paso 4 de 4</div>
        <h1>Tu paquete está listo</h1>
        <p>${r.productos} producto${r.productos === 1 ? '' : 's'} · ${pesoLegible(r.bytes)}</p>
      </div>

      <div class="kpis" style="margin-bottom:20px">
        <div class="kpi"><div class="kpi-label">Productos</div><div class="kpi-valor">${num(r.productos)}</div></div>
        <div class="kpi"><div class="kpi-label">Portadas</div><div class="kpi-valor">${r.portadas}<span class="kpi-sub"> / ${r.productos}</span></div></div>
        <div class="kpi"><div class="kpi-label">Errores</div><div class="kpi-valor" style="${v.resumen.errores ? 'color:var(--danger)' : ''}">${v.resumen.errores}</div></div>
        <div class="kpi"><div class="kpi-label">Avisos</div><div class="kpi-valor">${v.resumen.avisos}</div></div>
      </div>

      <a class="btn btn-primary btn-lg btn-block" href="${esc(r.descarga)}" download>⬇ Descargar ${esc(r.archivo)}</a>
    </div>

    ${panelValidacion(v)}

    <div class="barra-accion">
      <div class="resumen">El ZIP queda disponible mientras la app esté abierta.</div>
      <div class="acciones">
        <button class="btn btn-secondary" data-accion="volver-2">Elegir otros productos</button>
        <button class="btn btn-ghost" data-accion="volver-1">Otro artista</button>
      </div>
    </div>
  </div>`;
}

function panelValidacion(v) {
  if (v.apto && !v.resumen.avisos) {
    return alerta('ok', '✅', '<strong>Validación sin observaciones.</strong> No encontré nada que las distribuidoras suelan rechazar.');
  }

  const errores = v.hallazgos.filter((h) => h.nivel === 'error');
  const avisos = v.hallazgos.filter((h) => h.nivel === 'aviso');

  const cabecera = errores.length
    ? alerta('danger', '⛔', `<strong>${errores.length} error${errores.length === 1 ? '' : 'es'} que suelen causar rechazo.</strong>
        Conviene corregirlos antes de entregar. El detalle también está en
        <span class="mono">_Validacion pre-entrega.txt</span> dentro del ZIP.`)
    : alerta('ok', '✅', `<strong>Sin errores de rechazo.</strong> Hay ${avisos.length} aviso${avisos.length === 1 ? '' : 's'} para revisar.`);

  const grupo = (lista, titulo, abierto) => !lista.length ? '' : `
    <details class="acordeon" ${abierto ? 'open' : ''}>
      <summary>${titulo} (${lista.length})</summary>
      <div class="acordeon-body">
        <div class="tabla-wrap" style="max-height:320px">
          <table class="tabla">
            <thead><tr><th>Producto</th><th>Track</th><th>Qué pasa</th></tr></thead>
            <tbody>${lista.map((h) => `
              <tr>
                <td class="celda-sub">${esc(h.producto)}</td>
                <td class="celda-sub">${h.track ? esc(h.track) : '<span class="muted">—</span>'}</td>
                <td>${esc(h.mensaje)}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>
    </details>`;

  return `<div style="margin-top:16px">
    ${cabecera}
    <div style="margin-top:12px">
      ${grupo(errores, 'Errores', true)}
      ${grupo(avisos, 'Avisos', false)}
    </div>
  </div>`;
}

/* ------------------------------------------------------------ acciones */

const ACCIONES = {
  async 'guardar-clave'() {
    const clave = $('#clave').value.trim();
    const caja = $('#setup-error');
    if (!clave) { caja.innerHTML = alerta('danger', '⛔', 'Pegá la clave.'); return; }
    caja.innerHTML = `<div class="row" style="margin-top:12px"><span class="spinner"></span><span>Verificando…</span></div>`;
    try {
      await api('/api/clave', { clave });
      S.config = await api('/api/config');
      render();
    } catch (e) {
      caja.innerHTML = alerta('danger', '⛔', `<strong>No se pudo guardar.</strong><br>${esc(e.message)}`);
    }
  },

  async relevar() {
    const url = $('#url').value.trim();
    if (!url) { S.error = 'Pegá el link del canal.'; render(); return; }
    S.error = ''; S.ocupado = true; S.job = null; render();
    try {
      const { job } = await api('/api/relevar', { url, con_codigos: $('#con-codigos')?.checked !== false });
      const cat = await esperarJob(job, () => actualizarProgreso());
      adoptarCatalogo(cat);          // arranca con todo elegido
      S.ocupado = false;
      render();
    } catch (e) {
      S.ocupado = false;
      S.error = e.message === 'CANCELADO' ? '' : e.message;
      render();
    }
  },

  async cancelar() {
    if (S.job) { try { await api(`/api/job/${S.job.id}/cancelar`, {}); } catch (_) {} }
  },

  'volver-1'() { S.paso = 1; S.error = ''; S.resultado = null; render(); },
  'volver-2'() { S.paso = 2; S.error = ''; render(); },
  'volver-3'() { S.paso = 3; S.error = ''; render(); },
  'ir-3'() { S.paso = 3; S.error = ''; render(); },

  'sel-todo'() { productosFiltrados().forEach((p) => S.seleccion.add(p.id)); render(); },
  'sel-nada'() { productosFiltrados().forEach((p) => S.seleccion.delete(p.id)); render(); },

  async 'tidal-iniciar'() {
    try {
      const d = await api('/api/tidal/iniciar', {});
      S.tidal = { url: d.url, codigo: d.codigo, device_code: d.device_code };
      // Le abrimos el sitio de Tidal para que no tenga que copiar el link.
      window.open(d.url, '_blank', 'noopener');
      render();
    } catch (e) { S.error = e.message; render(); }
  },

  async 'tidal-confirmar'() {
    if (!S.tidal) return;
    try {
      const d = await api('/api/tidal/confirmar', { device_code: S.tidal.device_code });
      if (d.conectada) {
        S.tidal = null;
        S.config = await api('/api/config');
      } else if (d.estado === 'pendiente') {
        S.tidal.aviso = 'Todavía no me llegó la confirmación. Completá el acceso en Tidal y volvé a apretar.';
      } else {
        S.tidal.aviso = `Falló la conexión (${d.estado}). Probá de nuevo.`;
      }
      render();
    } catch (e) { S.error = e.message; render(); }
  },

  async 'tidal-salir'() {
    try { await api('/api/tidal/desconectar', {}); } catch (_) {}
    S.tidal = null;
    S.config = await api('/api/config');
    render();
  },

  async generar() {
    const ids = seleccionados().map((p) => p.id);
    if (!ids.length) { S.error = 'No hay productos elegidos.'; render(); return; }
    S.error = ''; S.resultado = null; S.ocupado = true; S.paso = 4; S.job = null; render();
    try {
      const { job } = await api('/api/preparar', {
        ids,
        planilla: S.opciones.planilla,
        portadas: S.opciones.portadas,
        audio: S.opciones.audio,
      });
      S.resultado = await esperarJob(job, () => actualizarProgreso());
      S.ocupado = false; render();
    } catch (e) {
      S.ocupado = false;
      S.error = e.message === 'CANCELADO' ? 'Cancelado.' : e.message;
      render();
    }
  },
};

/** Actualiza sólo el bloque de progreso, para no redibujar (ni perder el scroll
 *  del log) en cada consulta al backend. */
function actualizarProgreso() {
  const j = S.job;
  if (!j) return;
  const barra = $('.barra-fill');
  if (barra) barra.style.width = Math.round((j.progreso || 0) * 100) + '%';
  const cont = $('.barra');
  if (cont) cont.classList.toggle('barra-indef', !j.progreso);
  const msj = $('#progreso-mensaje');
  if (msj) msj.textContent = j.mensaje || 'Trabajando…';
  const log = $('#log');
  if (log) {
    const pegadoAbajo = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
    log.textContent = (j.log || []).slice(-60).join('\n');
    if (pegadoAbajo) log.scrollTop = log.scrollHeight;
  }
  $('#pie-estado').textContent = j.mensaje || '';
}

/* ------------------------------------------------------------ eventos */

document.addEventListener('click', (ev) => {
  const btnAccion = ev.target.closest('[data-accion]');
  if (btnAccion) {
    const fn = ACCIONES[btnAccion.dataset.accion];
    if (fn) { ev.preventDefault(); fn(); }
    return;
  }

  const modo = ev.target.closest('[data-modo]');
  if (modo) {
    S.filtro.modo = modo.dataset.modo;
    render();
    return;
  }

  const expandir = ev.target.closest('[data-expandir]');
  if (expandir) {
    const id = expandir.dataset.expandir;
    if (S.expandidos.has(id)) S.expandidos.delete(id); else S.expandidos.add(id);
    conScrollPreservado(render);
    return;
  }

  // Clic en la tarjeta de opción (no en su checkbox) también la alterna.
  const tarjeta = ev.target.closest('[data-opcion]');
  if (tarjeta && ev.target.tagName !== 'INPUT' && !tarjeta.classList.contains('deshabilitada')) {
    const k = tarjeta.dataset.opcion;
    S.opciones[k] = !S.opciones[k];
    render();
  }
});

document.addEventListener('change', async (ev) => {
  const t = ev.target;

  if (t.dataset.prod) {
    if (t.checked) S.seleccion.add(t.dataset.prod); else S.seleccion.delete(t.dataset.prod);
    // Actualización puntual: redibujar la tabla entera en cada clic se siente lento.
    const fila = document.querySelector(`[data-fila="${t.dataset.prod}"]`);
    if (fila) fila.classList.toggle('elegida', t.checked);
    actualizarResumenSeleccion();
    return;
  }

  if (t.id === 'check-todos') {
    if (t.checked) productosFiltrados().forEach((p) => S.seleccion.add(p.id));
    else productosFiltrados().forEach((p) => S.seleccion.delete(p.id));
    conScrollPreservado(render);
    return;
  }

  if (t.dataset.distrib !== undefined) {
    if (t.checked) S.filtro.distribs.add(t.dataset.distrib);
    else S.filtro.distribs.delete(t.dataset.distrib);
    render();
    return;
  }

  if (t.dataset.opcionCheck) {
    S.opciones[t.dataset.opcionCheck] = t.checked;
    render();
    return;
  }

  if (t.id === 'anio-desde' || t.id === 'anio-hasta') {
    S.filtro.anioDesde = parseInt($('#anio-desde').value, 10);
    S.filtro.anioHasta = parseInt($('#anio-hasta').value, 10);
    render();
  }
});

document.addEventListener('input', (ev) => {
  if (ev.target.id === 'buscar') {
    S.filtro.texto = ev.target.value;
    clearTimeout(document.__buscarTimer);
    // Debounce: filtrar en cada tecla sobre un catálogo grande se nota.
    // El foco y el cursor los recupera render() por su cuenta.
    document.__buscarTimer = setTimeout(render, 180);
  }
});

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && ev.target.id === 'url') { ev.preventDefault(); ACCIONES.relevar(); }
  if (ev.key === 'Enter' && ev.target.id === 'clave') { ev.preventDefault(); ACCIONES['guardar-clave'](); }
});

/** Redibuja conservando el scroll de la tabla. */
function conScrollPreservado(fn) {
  const prev = $('#tabla-wrap')?.scrollTop || 0;
  fn();
  const nuevo = $('#tabla-wrap');
  if (nuevo) nuevo.scrollTop = prev;
}

function actualizarResumenSeleccion() {
  const ps = productosFiltrados();
  const sel = seleccionados();
  const caja = document.querySelector('.barra-accion .resumen');
  if (caja) {
    caja.innerHTML = `<strong>${sel.length} de ${ps.length}</strong> productos elegidos · ${sel.reduce((a, p) => a + p.tracks, 0)} tracks`;
  }
  const btn = document.querySelector('[data-accion="ir-3"]');
  if (btn) btn.disabled = sel.length === 0;
  const todos = $('#check-todos');
  if (todos) {
    todos.checked = ps.length > 0 && ps.every((p) => S.seleccion.has(p.id));
    todos.indeterminate = !todos.checked && ps.some((p) => S.seleccion.has(p.id));
  }
}

/* ------------------------------------------------------------ arranque */

/** Carga el catálogo que el backend ya tenga en memoria. Sirve para que un F5
 *  no obligue a relevar de nuevo, que cuesta cuota de YouTube. */
function adoptarCatalogo(cat) {
  S.catalogo = cat;
  S.seleccion = new Set(cat.productos.map((p) => p.id));
  S.expandidos = new Set();
  S.filtro = {
    modo: 'manual',
    anioDesde: cat.filtros.anio_min,
    anioHasta: cat.filtros.anio_max,
    distribs: new Set(cat.filtros.distribuidoras.map((d) => d.name)),
    texto: '',
  };
  S.paso = 2;
}

(async function iniciar() {
  render();
  try {
    S.config = await api('/api/config');
  } catch (e) {
    pantalla().innerHTML = alerta('danger', '⛔',
      '<strong>No pude conectar con el motor de la app.</strong><br>Cerrala y volvé a abrirla.');
    return;
  }

  if (S.config.catalogo_cargado) {
    try { adoptarCatalogo(await api('/api/catalogo')); } catch (_) { /* seguimos en el paso 1 */ }
  }

  render();
  const i = $('#url');
  if (i) i.focus();
})();

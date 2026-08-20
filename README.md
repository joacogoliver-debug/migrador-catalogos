# Migrador de Catálogos

App gratuita para relevar el catálogo de un artista y preparar su migración a
otra distribuidora. Pegás el link del canal de YouTube y te devuelve los
**ISRC**, los **UPC**, las **portadas** en alta resolución, una **hoja de
ingesta** lista para cargar y una **validación pre-entrega** que te dice qué va a
ser rechazado antes de que lo mandes.

Hecha por [Mojo Latam](https://mojo.com.ar). Código abierto y sin costo.

![tests](https://github.com/joacogoliver-debug/migrador-catalogos/actions/workflows/tests.yml/badge.svg)

---

## Descargar

Bajá el archivo de tu sistema desde [Releases](https://github.com/joacogoliver-debug/migrador-catalogos/releases)
y abrilo. No hay instalador ni dependencias.

| Sistema | Archivo |
|---|---|
| Windows | `Migrador-de-Catalogos-windows.exe` |
| macOS | `Migrador-de-Catalogos-macos` |
| Linux | `Migrador-de-Catalogos-linux` |

### El sistema va a decir que el programa "no es reconocido"

Es esperable y no significa que haya algo mal. El binario **no está firmado**
porque un certificado de firma cuesta plata por año y esta herramienta es
gratis. Cómo seguir:

- **Windows**: "Más información" → "Ejecutar de todas formas".
- **macOS**: clic derecho → "Abrir", o Configuración → Privacidad y seguridad →
  "Abrir de todos modos".

En vez de una firma paga, la confianza se apoya en tres cosas verificables: el
código es público, **el ejecutable se compila acá en GitHub Actions** desde ese
código a la vista de todos, y cada release publica el SHA256 más una atestación
de procedencia. Podés verificar que el binario salió de este repo:

```bash
gh attestation verify Migrador-de-Catalogos-windows.exe --repo joacogoliver-debug/migrador-catalogos
```

Es la misma postura que usa `yt-dlp`.

### La primera vez

La app te va a pedir una clave de la **YouTube Data API v3**. Es gratis: se saca
en [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
creando un proyecto, activando *YouTube Data API v3* y generando una clave de
API. La app la verifica con una consulta de prueba y la guarda **en tu máquina**
(`~/.migrador-catalogos/config.json`). El cupo diario alcanza para unos 500
catálogos.

---

## Cómo se usa

**1. Pegás el link** del canal, Topic o `@handle` del artista.

**2. Elegís qué productos migrar.** El catálogo aparece agrupado en productos
(álbum / EP / single). Podés marcarlos uno por uno, filtrar por rango de años,
por distribuidora, o buscar por título, ISRC o UPC. Cada producto se puede
desplegar para ver sus tracks.

**3. Elegís qué descargar**: planilla y validación, portadas, y audios si
activaste ese módulo.

**4. Descargás un ZIP** organizado con una carpeta por producto.

```
Artista - Migracion 2026-08-14/
├── _Validacion pre-entrega.txt    ← empezá por acá
├── _Hoja de ingesta.csv           ← el archivo para cargar en la distribuidora
├── _Catalogo completo.xlsx
├── _Reporte de migracion.txt
├── _LEEME.txt
└── 2013 - Random Access Memories [886443919259]/
    ├── portada.jpg
    └── datos.xlsx
```

Es una carpeta por producto porque en una migración cada release se entrega como
una unidad: un UPC, una portada, sus datos. Así cada carpeta ya queda lista para
subir, y el UPC en el nombre evita confundir un álbum con su reedición.

## La validación pre-entrega

Es la parte que más tiempo ahorra. Separa lo que causa rechazo de lo que sólo
conviene revisar.

**Errores** (la distribuidora los rechaza)
- ISRC con formato inválido
- UPC con dígito verificador incorrecto o largo equivocado
- ISRC repetido entre tracks, o UPC repetido entre productos
- Portada no cuadrada, por debajo de 1400×1400, o en CMYK
- Año de lanzamiento en el futuro o imposible
- Track sin título o sin duración

**Avisos** (pasan la ingesta, conviene mirarlos)
- Falta un ISRC o un UPC: se va a asignar uno nuevo y se pierde el historial de
  la grabación o la continuidad del release
- El título arrastra texto de YouTube (`(Official Video)`, `[Lyric Video]`…)
- El orden de los tracks es estimado y no está confirmado
- Falta el sello (℗)
- La portada entra pero está por debajo de los 3000×3000 recomendados

Los códigos se validan por sus reglas reales: formato ISRC de 12 caracteres y
dígito verificador GTIN de UPC-A y EAN-13.

## De dónde salen los datos

| Dato | Fuente | Necesita clave |
|---|---|---|
| Catálogo, distribuidora, sello, año, reproducciones | YouTube Data API | sí (la tuya) |
| ISRC y UPC | [Deezer](https://developers.deezer.com/api) | no |
| Portadas | [iTunes Search API](https://performance-partners.apple.com/search-api) | no |

Las portadas se piden en 3000×3000, pero **se reporta la resolución que Apple
efectivamente devolvió**, no la que pedimos: Apple sirve el máximo que tiene para
ese release y responde igual aunque sea más chico. Si dijéramos 3000×3000 sobre
una portada de 600×600, la planilla afirmaría que cumple el mínimo de ingesta
cuando en realidad la van a rechazar.

## Qué NO hace

- **No baja audio en la versión que se descarga.** El módulo existe pero viene
  apagado y sus dependencias no están dentro del ejecutable (ver abajo).
- **No genera DDEX ERN.** Emitir ERN válido requiere ser una parte registrada de
  DDEX con identificador propio, así que un XML "casi DDEX" se rechazaría igual
  dando falsa sensación de que está listo. En su lugar generamos un CSV con las
  columnas estándar que aceptan o mapean casi todas las distribuidoras.
- **No inventa metadata.** Lo que no puede salir de fuentes públicas (género,
  explicit, compositores, editoriales, línea ©) queda marcado `<<COMPLETAR>>` en
  la hoja de ingesta, no vacío ni rellenado a ojo.
- **No adivina el número de track.** YouTube no lo expone: el orden se estima por
  fecha de subida y se marca como no confirmado.
- **No sabe el formato del release.** Single / EP / álbum se deduce de la cantidad
  de tracks (1-3 / 4-6 / 7+), que es la convención de las distribuidoras pero
  sigue siendo una heurística.

## Privacidad

- **No hay cuentas, registro ni servidor nuestro.** La app corre entera en tu
  máquina: levanta un servidor local en `127.0.0.1` que no queda expuesto en la
  red.
- **Tu clave de la API queda en tu computadora**, nunca se manda a ningún lado
  que no sea Google.
- **No se guarda el catálogo.** Vive en memoria mientras la app está abierta.
- Deezer e iTunes se consultan con datos públicos del catálogo (nombre de
  artista, título, duración).

---

## Correrla desde el código

```bash
pip install -r requirements-app.txt
python app/launcher.py
```

En Windows podés usar `ABRIR_APP.bat`; en macOS/Linux, `./abrir_app.sh`. Los dos
instalan las dependencias la primera vez.

La app abre en **su propia ventana**, no en el navegador: usa `pywebview` sobre
el motor web del sistema (WebView2 en Windows, WebKit en macOS). Si por algo no
puede, cae al motor del sistema en modo aplicación —ventana propia, sin barra de
direcciones ni pestañas— y si tampoco, al navegador. `--diagnostico` escribe un
reporte de qué puede hacer la app en esa máquina, útil porque el ejecutable se
compila sin consola.

### Compilar el ejecutable

```bash
pip install pyinstaller
python build/build.py
```

Corre los tests, empaqueta con [build/migrador.spec](build/migrador.spec) y deja
el binario y su SHA256 en `dist/`.

### Módulo de audio (opcional, apagado por defecto)

La app puede además bajar los audios, pero **no viene activado** y sus
dependencias **no están dentro del ejecutable** a propósito. Se usa corriendo
desde el código:

```bash
pip install -r requirements-audio.txt   # tiddl + yt-dlp
# y ffmpeg en el PATH: https://ffmpeg.org/download.html
MIGRADOR_AUDIO=1 python app/launcher.py
```

Funciona en dos niveles, y cada archivo queda etiquetado por lo que realmente es:

| Nivel | Fuente | Formato | ¿Apto para entrega? |
|---|---|---|---|
| A | Tidal, con **tu propia** cuenta paga | FLAC lossless | sí |
| B | YouTube | Opus / M4A | **no**, sólo referencia |

Dos cosas que importan:

- **El audio de YouTube es lossy y no se puede arreglar.** YouTube recodifica
  todo lo que se sube. Pasarlo a WAV multiplica el peso sin recuperar nada, así
  que la app guarda el stream original en vez de fingir calidad.
- **Se verifica el codec real de cada archivo**, no la calidad pedida. Tidal
  puede servir AAC para grabaciones sin máster lossless incluso cuando se pide
  LOSSLESS. Todo lo que no sea FLAC queda marcado en la planilla, en el nombre
  del archivo (`[REFERENCIA-LOSSY]`) y en el reporte.

Para una entrega formal lo correcto sigue siendo el **máster original** del
artista o del sello. Esto es un respaldo para cuando ese archivo no aparece.

Si conectás Tidal, el login es por device-code: la app te manda al sitio de Tidal
y **tu contraseña nunca pasa por acá**. El token vive sólo en la sesión y se borra
al cerrarla; del perfil se guarda únicamente el ID de usuario y el país.

---

## Cómo está hecha

Sin frameworks ni build step, para que empaquetar sea copiar archivos:

- **Backend**: `http.server` de la biblioteca estándar. Es una app local de un
  usuario, así que no hacen falta ASGI ni workers, y a cambio el ejecutable no
  depende de los imports dinámicos de uvicorn, que son la causa habitual de que
  un binario ande en desarrollo y falle empaquetado.
- **Frontend**: JavaScript vanilla y CSS sobre los tokens del design system de
  Mojo. Nada de CDN: la app funciona sin internet una vez abierta.
- **Trabajos largos** (relevar, empaquetar) corren en hilos con progreso y
  cancelación; el frontend consulta el estado cada 400 ms.

| Archivo | Qué hace |
|---|---|
| `app/launcher.py` | Punto de entrada: puerto libre, servidor, ventana o navegador |
| `app/server.py` | API JSON y servidor de estáticos |
| `app/jobs.py` | Trabajos en segundo plano con progreso y cancelación |
| `app/web/` | La interfaz (html, css, js) |
| `app/web/tokens/` | Paleta, tipografía y espaciado |
| `migrar_core.py` | Orquesta los 4 pasos |
| `relevar_core.py` | Relevamiento de YouTube + ISRC/UPC por Deezer |
| `productos.py` | Agrupa tracks en productos y filtra la selección |
| `validar.py` | Validación pre-entrega |
| `portadas.py` | Portadas vía iTunes Search API |
| `paquete.py` | Planillas, hoja de ingesta, reportes y ZIP |
| `audio.py` | Módulo de audio opcional |
| `app.py`, `app_migrar.py` | Versiones Streamlit anteriores, para desplegar como web |

## Tests

Corren sin red, sin claves y sin las dependencias de audio:

```bash
python test_parse_description.py   # parseo de descripciones de YouTube
python test_productos.py           # agrupación en productos + filtros
python test_validar.py             # validación de códigos, portadas y duplicados
python test_portadas.py            # resolución real de las portadas
python test_paquete.py             # estructura del ZIP + etiquetado de calidad
python test_app.py                 # backend: trabajos + servidor HTTP
```

CI los corre en cada push, a propósito **sin** instalar `tiddl`/`yt-dlp`/`ffmpeg`,
para verificar que el núcleo no dependa de ellos.

## Créditos

- La técnica para pedir portadas en alta resolución al CDN de Apple viene de
  [`fchavonet/full_stack-itunes_artwork_finder`](https://github.com/fchavonet/full_stack-itunes_artwork_finder).
- El módulo opcional de audio lossless usa
  [`oskvr37/tiddl`](https://github.com/oskvr37/tiddl) (Apache 2.0) como librería.

## Licencia

MIT — ver [LICENSE](LICENSE).

Esta herramienta es para que dueños de catálogo releven y migren **su propio**
material. Cada usuario es responsable de tener los derechos sobre el contenido
que procesa y de cumplir los términos de los servicios que consulta.

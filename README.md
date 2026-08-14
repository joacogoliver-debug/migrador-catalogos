# Migrador de Catálogos

Herramienta gratuita para relevar el catálogo de un artista y preparar su
migración a otra distribuidora. Pegás el link del canal de YouTube y te devuelve
los **ISRC**, los **UPC**, las **portadas** en alta resolución, una **hoja de
ingesta** lista para cargar y una **validación pre-entrega** que te dice qué va a
ser rechazado antes de que lo mandes.

Hecha por [Mojo Latam](https://mojo.com.ar). Código abierto, sin costo y sin
cuenta: la usás desde el navegador o la corrés en tu máquina.

![tests](https://github.com/OWNER/REPO/actions/workflows/tests.yml/badge.svg)

---

## Qué hace

1. **Pegás el link** del canal, Topic o `@handle` del artista en YouTube.
2. **Te muestra el catálogo dividido en productos** (álbum / EP / single). Elegís
   qué migrar: uno por uno, por rango de fechas, o por distribuidora.
3. **Elegís qué querés bajar**: la planilla con datos y códigos, y/o las portadas.
4. **Te devuelve un ZIP organizado por producto**, con una carpeta por release.

```
Artista - Migracion 2026-08-14/
├── _Validacion pre-entrega.txt    ← empezá por acá
├── _Hoja de ingesta.csv           ← el archivo para cargar en la distribuidora
├── _Catalogo completo.xlsx
├── _Reporte de migracion.txt
├── _LEEME.txt
└── 2019 - Nombre del Album [UPC]/
    ├── portada.jpg
    └── datos.xlsx
```

Es una carpeta por producto porque en una migración cada release se entrega como
una unidad: un UPC, una portada, sus datos. Así cada carpeta ya queda lista para
subir. El UPC en el nombre evita confundir un álbum con su reedición.

## La validación pre-entrega

Es la parte que más tiempo ahorra. Separa lo que causa rechazo de lo que sólo
conviene revisar:

**Errores** (la distribuidora los rechaza)
- ISRC con formato inválido
- UPC con dígito verificador incorrecto o largo equivocado
- ISRC repetido entre tracks, o UPC repetido entre productos
- Portada no cuadrada, por debajo de 1400×1400, o en CMYK
- Año de lanzamiento en el futuro o imposible
- Track sin título o sin duración

**Avisos** (pasan la ingesta, conviene mirarlos)
- Falta un ISRC o un UPC: la distribuidora va a asignar uno nuevo y se pierde el
  historial de la grabación o la continuidad del release
- El título arrastra texto de YouTube (`(Official Video)`, `[Lyric Video]`…)
- El orden de los tracks es estimado y no está confirmado
- Falta el sello (℗)
- La portada entra pero está por debajo de los 3000×3000 recomendados

Los códigos se validan por sus reglas reales (formato ISRC de 12 caracteres,
dígito verificador GTIN de UPC-A y EAN-13), no por aproximación.

## De dónde salen los datos

| Dato | Fuente | Necesita clave |
|---|---|---|
| Catálogo, distribuidora, sello, año, reproducciones | YouTube Data API | sí (del servidor) |
| ISRC y UPC | [Deezer](https://developers.deezer.com/api) | no |
| Portadas | [iTunes Search API](https://performance-partners.apple.com/search-api) | no |

Las portadas se piden en 3000×3000, pero **se reporta la resolución que Apple
efectivamente devolvió**, no la que pedimos: Apple sirve el máximo que tiene para
ese release y responde igual aunque sea más chico. Si te dijéramos 3000×3000
sobre una portada de 600×600, la planilla afirmaría que cumple el mínimo de
ingesta cuando en realidad la van a rechazar.

## Qué NO hace

Vale aclararlo para que nadie pierda tiempo:

- **No baja audio en la versión pública.** El módulo de audio existe pero viene
  apagado y es opcional (ver abajo).
- **No genera DDEX ERN.** Emitir ERN válido requiere ser una parte registrada de
  DDEX con un identificador propio, así que un XML "casi DDEX" se rechazaría
  igual dando falsa sensación de que está listo. En su lugar generamos un CSV con
  las columnas estándar que aceptan o mapean casi todas las distribuidoras.
- **No inventa metadata.** Lo que no puede salir de fuentes públicas (género,
  explicit, compositores, editoriales, línea ©) queda marcado `<<COMPLETAR>>` en
  la hoja de ingesta, no vacío ni rellenado a ojo.
- **No adivina el número de track.** YouTube no lo expone. El orden se estima por
  fecha de subida y se marca como no confirmado.
- **No sabe el formato del release.** Single / EP / álbum se deduce de la cantidad
  de tracks (1-3 / 4-6 / 7+), que es la convención de las distribuidoras pero
  sigue siendo una heurística.

## Privacidad

- **No hay cuentas ni registro.** No te pedimos email ni datos personales.
- **No se guarda nada.** El catálogo relevado vive en la sesión del navegador y
  se descarta al cerrarla. No hay base de datos.
- **La clave de YouTube es del servidor** y nunca se expone al cliente.
- Deezer y iTunes se consultan con datos públicos del catálogo (nombre de
  artista, título, duración). No se les manda nada tuyo.

## Correrla en tu máquina

```bash
pip install -r requirements.txt
streamlit run app_migrar.py
```

Necesitás una clave de la YouTube Data API v3 (gratis, desde Google Cloud
Console) en `.streamlit/secrets.toml`:

```toml
YOUTUBE_API_KEY = "AIza..."
APP_PASSWORD = ""   # vacío = sin contraseña, para uso local
```

Consumo de cuota: ~12-21 unidades por catálogo, de las 10.000/día que da Google.

## Módulo de audio (opcional, apagado por defecto)

La herramienta puede además bajar los audios, pero **no viene activado** y no es
parte de la instalación normal. Se prende sólo si lo instalás vos, en tu máquina:

```bash
pip install -r requirements-audio.txt   # tiddl + yt-dlp
# y ffmpeg en el PATH: https://ffmpeg.org/download.html
MIGRADOR_AUDIO=1 streamlit run app_migrar.py
```

Funciona en dos niveles, y cada archivo queda etiquetado por lo que realmente es:

| Nivel | Fuente | Formato | ¿Apto para entrega? |
|---|---|---|---|
| A | Tidal, con **tu propia** cuenta paga | FLAC lossless | sí |
| B | YouTube | Opus / M4A | **no**, sólo referencia |

Dos cosas que importan acá:

- **El audio de YouTube es lossy y no se puede arreglar.** YouTube recodifica todo
  lo que se sube. Pasarlo a WAV multiplica el peso sin recuperar nada, así que la
  herramienta guarda el stream original en vez de fingir calidad.
- **Se verifica el codec real de cada archivo**, no la calidad pedida. Tidal
  puede servir AAC para grabaciones sin máster lossless incluso cuando se pide
  LOSSLESS. Todo lo que no sea FLAC queda marcado en la planilla, en el nombre del
  archivo (`[REFERENCIA-LOSSY]`) y en el reporte.

Para una entrega formal lo correcto sigue siendo el **máster original** del
artista o del sello. Esto es un respaldo para cuando ese archivo no aparece.

Si conectás Tidal, el login es por device-code: la app te manda al sitio de Tidal
y **tu contraseña nunca pasa por acá**. El token vive sólo en la sesión y se borra
al cerrarla; del perfil se guarda únicamente el ID de usuario y el país, que es lo
que la API necesita.

## Tests

Corren sin red, sin claves y sin las dependencias de audio:

```bash
python test_parse_description.py   # parseo de descripciones de YouTube
python test_productos.py           # agrupación en productos + filtros
python test_validar.py             # validación de códigos, portadas y duplicados
python test_portadas.py            # resolución real de las portadas
python test_paquete.py             # estructura del ZIP + etiquetado de calidad
```

CI los corre en cada push, a propósito **sin** instalar `tiddl`/`yt-dlp`/`ffmpeg`,
para verificar que el núcleo no dependa de ellos.

## Estructura

| Archivo | Qué hace |
|---|---|
| `app_migrar.py` | La interfaz de 4 pasos (Streamlit) |
| `migrar_core.py` | Orquesta los 4 pasos |
| `relevar_core.py` | Relevamiento de YouTube + ISRC/UPC por Deezer |
| `productos.py` | Agrupa tracks en productos y filtra la selección |
| `validar.py` | Validación pre-entrega |
| `portadas.py` | Portadas vía iTunes Search API |
| `paquete.py` | Planillas, hoja de ingesta, reportes y ZIP |
| `audio.py` | Módulo de audio opcional |
| `app.py` | El relevador original, más simple (sólo Excel) |

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

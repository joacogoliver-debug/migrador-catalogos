# Migrador de catálogos

Herramienta para migrar el catálogo de un artista de una distribuidora a otra.
Se construye sobre el relevador existente (`relevar_core.py`) y agrega
agrupación en productos, portadas, audio y empaquetado.

## Flujo de 4 pasos

1. **Pegás el link de YouTube** del artista (Topic, OAC o @handle).
2. **La app devuelve el catálogo dividido en productos** (álbum / EP / single).
   Se selecciona a mano, por rango de fechas o por distribuidora.
3. **Elegís qué bajar**: planilla con datos y códigos / audios / portadas.
4. **La app devuelve un ZIP organizado por producto**, listo para entregar.

## Estructura del entregable

```
Artista - Migracion 2026-08-13/
├── _LEEME.txt                     Cómo está organizado + aviso de calidad
├── _Catalogo completo.xlsx        Planilla maestra de todos los productos
├── _Reporte de migracion.txt      Qué salió, qué faltó y por qué
├── 2019 - Nombre del Album [UPC]/
│   ├── portada.jpg                Hasta 3000x3000
│   ├── datos.xlsx                 Sólo este producto, con ISRCs
│   ├── 01 - Primer Tema.flac
│   └── 02 - Segundo Tema.flac
└── 2021 - Nombre del Single [UPC]/
    └── ...
```

Es por producto y no por tipo de archivo porque en una migración cada producto se
entrega como una unidad (un UPC, una portada, sus audios): así cada carpeta ya
queda lista para subir. El UPC en el nombre evita confundir un álbum con su
reedición.

## Calidad de audio — los dos niveles

Esta es la parte que hay que entender antes de entregar nada.

| Nivel | Fuente | Formato | ¿Apto para entrega? |
|---|---|---|---|
| **A** | Tidal (cuenta propia del usuario) | FLAC 16/44.1 o 24/192 | **Sí** |
| **B** | YouTube | Opus / M4A (~128-160 kbps) | **No** — sólo referencia |

**El audio de YouTube es lossy y no se puede "arreglar".** YouTube re-codifica
todo lo que se sube, así que ese audio ya viene con pérdida. Pasarlo a WAV
multiplicaría el peso por ~50 sin recuperar nada de lo que se perdió: por eso la
herramienta **no** convierte a WAV, guarda el stream original.

**Tidal sí entrega lossless real**, porque distribuye el máster. Pero hay un caso
borde importante: Tidal puede servir AAC para grabaciones que no tienen máster
lossless, incluso cuando se pide LOSSLESS. Por eso la herramienta **verifica el
codec real de cada archivo bajado** en vez de confiar en la calidad pedida, y
etiqueta según lo que efectivamente llegó.

Todo lo que no sea FLAC queda marcado en tres lugares, para que no se cuele en
una entrega por distracción:
- En la planilla, columna "Fuente / Calidad", resaltado en ámbar.
- En el nombre del archivo: `01 - Tema [REFERENCIA-LOSSY].m4a`.
- En el reporte de migración, listado producto por producto.

> Para una entrega formal, lo más limpio sigue siendo el máster original del
> artista o del sello, o un export de assets desde la distribuidora actual.
> Tidal FLAC es un muy buen fallback cuando eso no aparece.

## Matcheo por ISRC (y por qué importa)

El relevamiento saca los ISRC vía Deezer. Con esos códigos, el módulo de audio
baja la **discografía completa del artista en Tidal** y cruza por ISRC.

Eso significa **match exacto por código**, no comparación de títulos. Es mucho
más confiable que el matcheo por texto, y de paso trae dos cosas que YouTube no
da: el **número de track real** (que resuelve el orden del álbum) y el **UPC**
del release para validar el que ya teníamos.

Cuando un producto no logra confirmar su orden, el reporte lo dice
explícitamente en vez de dar un orden inventado por bueno.

## Cuentas y privacidad

La herramienta la usan clientes y artistas externos, así que:

- **El relevamiento, las planillas y las portadas no piden ninguna credencial al
  usuario.** Usan la clave de YouTube del servidor y APIs públicas (Deezer,
  iTunes). Funcionan para cualquiera, sin configurar nada.
- **El audio en calidad de entrega requiere que cada usuario conecte su propia
  cuenta de Tidal.** No se comparte una cuenta entre usuarios: Tidal permite ~1
  stream simultáneo por cuenta (dos personas bajando a la vez se cortan entre
  sí), compartir cuenta va contra sus términos, y si Tidal bloquea la cuenta
  compartida se cae la herramienta para todos a la vez.
- **El login es device-code**: la app muestra un código, la persona se autentica
  en el sitio de Tidal. **Su contraseña nunca pasa por la app.**
- **El token vive sólo en la sesión**, con cache en un directorio propio que se
  borra en `close()`. No se escribe a disco ni se toca el `~/.tiddl` global.
- **Del perfil de Tidal se guarda sólo `user_id` y `country_code`**, que es lo
  que la API necesita. El login devuelve también email, nombre, dirección y
  teléfono: eso se descarta a propósito.

## Módulos

| Archivo | Qué hace | Depende de |
|---|---|---|
| `relevar_core.py` | Relevamiento YouTube + ISRC/UPC por Deezer (ya existía) | clave de YouTube |
| `productos.py` | Agrupa tracks en productos + filtros de selección | nada |
| `portadas.py` | Portadas vía iTunes Search API | nada |
| `audio.py` | Sesión de Tidal, índice ISRC, descarga FLAC + referencia | tiddl, yt-dlp, ffmpeg |
| `paquete.py` | Planillas, reporte y ZIP del entregable | openpyxl |
| `migrar_core.py` | Orquesta los 4 pasos | los de arriba |
| `app_migrar.py` | La interfaz web (Streamlit) del flujo de 4 pasos | streamlit |

## Datos que son estimados (y hay que verificar)

YouTube no declara todo lo que necesita una ficha de release. Dos campos son
heurísticas, y tanto el LEEME como el reporte del paquete lo aclaran:

- **Tipo (single / EP / álbum)**: se deduce de la cantidad de tracks siguiendo la
  convención de las distribuidoras (1-3 single, 4-6 EP, 7+ álbum). Un EP corto
  puede figurar como single.
- **Orden de los tracks**: si el producto se cruzó con Tidal por ISRC, el número
  de track es el real. Si no, es un estimado por fecha de subida y el reporte lo
  marca como "sin confirmar" en vez de darlo por bueno.

### Origen de las piezas de terceros

- **Portadas**: lógica portada de
  [`fchavonet/full_stack-itunes_artwork_finder`](https://github.com/fchavonet/full_stack-itunes_artwork_finder)
  (el truco de reescribir la URL del CDN de Apple para pedir alta resolución).
- **Audio lossless**: [`oskvr37/tiddl`](https://github.com/oskvr37/tiddl)
  (Apache 2.0), usado **como librería** y no por CLI, para poder aislar la sesión
  por usuario en vez de usar su config global de un solo usuario.
- **Audio de referencia**: `yt-dlp`.

> Nota: se descartó `Adil0095/symphonic-link-archiver`. No contiene código de
> descarga: su README es relleno generado que describe una app inexistente, su
> único archivo real es un script ofuscado (base64 + XOR) que reescribe la página
> en runtime, y tiene un GitHub Action que hace commits falsos cada hora para
> simular actividad. No usarlo.

## Cómo correrla

```bash
streamlit run app_migrar.py
```

El relevador original sigue funcionando aparte con `streamlit run app.py`.

## Instalación

```bash
pip install -r requirements.txt
```

Para el audio hace falta además **ffmpeg** en el PATH (no es un paquete de pip):
https://ffmpeg.org/download.html

La app detecta qué hay disponible con `audio.verificar_entorno()` y no ofrece
opciones que no puedan funcionar.

## Tests

Offline, sin claves ni red. Corren en CI en cada push.

```bash
python test_parse_description.py   # parseo de descripciones de YouTube
python test_productos.py           # agrupación en productos + filtros
python test_paquete.py             # estructura del ZIP + etiquetado de calidad
```

El CI corre a propósito **sin** tiddl/yt-dlp/ffmpeg instalados: eso verifica que
la degradación elegante funcione y que el núcleo no dependa de los extras.

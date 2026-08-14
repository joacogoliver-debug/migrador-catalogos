"""
Trabajos en segundo plano con progreso y cancelación.

Relevar un catálogo tarda entre 20 segundos y varios minutos, y armar el paquete
puede tardar más. Si eso corriera dentro del request, la interfaz quedaría
congelada sin poder mostrar avance ni dejar cancelar. Así que cada operación
larga se corre en un hilo y el frontend consulta el estado.

Se usa polling en vez de websockets o SSE a propósito: es una app local de un
solo usuario, el polling cada 400 ms es imperceptible y no agrega dependencias
ni estados de conexión que puedan quedar colgados.
"""

import threading
import time
import traceback
import uuid

# Cuánto se conserva un trabajo terminado antes de descartarlo. Tiene que
# alcanzar para que el usuario descargue el ZIP con calma.
TTL_TERMINADO = 60 * 60          # 1 hora
MAX_LOG = 400                    # líneas de log que guardamos por trabajo


class Cancelado(Exception):
    """La cancelación pedida por el usuario se propaga como excepción."""


class Job:
    """Un trabajo en curso. El frontend lo lee por /api/job/<id>."""

    def __init__(self, tipo):
        self.id = uuid.uuid4().hex[:12]
        self.tipo = tipo
        self.estado = "pendiente"        # pendiente|corriendo|listo|error|cancelado
        self.progreso = 0.0
        self.mensaje = ""
        self.log = []
        self.resultado = None
        self.error = ""
        self.creado = time.time()
        self.terminado_en = None
        self._cancelar = threading.Event()
        self._lock = threading.Lock()

    # ---- lo que usa el código que corre dentro del trabajo ----

    def avance(self, mensaje, fraccion=None):
        """Reporta avance. Acepta `progress(msg, frac)` y `log(msg)` para poder
        pasarse tal cual a los dos estilos de callback que usan los módulos."""
        self.abortar_si_cancelado()
        with self._lock:
            self.mensaje = str(mensaje)
            if fraccion is not None:
                self.progreso = max(0.0, min(1.0, float(fraccion)))
            self.log.append(str(mensaje))
            if len(self.log) > MAX_LOG:
                # Conservamos el arranque y la cola: el medio de un log largo
                # casi nunca aporta y no queremos crecer sin límite.
                del self.log[1:len(self.log) - MAX_LOG + 1]

    def abortar_si_cancelado(self):
        if self._cancelar.is_set():
            raise Cancelado()

    @property
    def cancelado(self):
        return self._cancelar.is_set()

    # ---- control desde afuera ----

    def cancelar(self):
        self._cancelar.set()

    def a_dict(self, con_log=False):
        with self._lock:
            d = {
                "id": self.id,
                "tipo": self.tipo,
                "estado": self.estado,
                "progreso": round(self.progreso, 3),
                "mensaje": self.mensaje,
                "error": self.error,
            }
            if con_log:
                d["log"] = list(self.log)
            if self.estado == "listo":
                d["resultado"] = self.resultado
            return d


class Registry:
    """Registro de trabajos en memoria. Es una app local de un usuario, así que
    no hace falta persistirlos: si se cierra la app, se van."""

    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def lanzar(self, tipo, fn):
        """Corre fn(job) en un hilo y devuelve el Job."""
        job = Job(tipo)
        with self._lock:
            self._jobs[job.id] = job

        def correr():
            job.estado = "corriendo"
            # `estado` se escribe SIEMPRE al final de cada rama: el frontend
            # consulta el trabajo cada 400 ms y usa el estado para saber si ya
            # puede leer el resto. Si lo marcáramos terminado antes de completar
            # resultado/error/log, una consulta que caiga justo en el medio leería
            # un error sin mensaje o un resultado a medio armar.
            try:
                resultado = fn(job)
                job.resultado = resultado
                job.progreso = 1.0
                if not job.mensaje:
                    job.mensaje = "Listo."
                job.terminado_en = time.time()
                job.estado = "listo"
            except Cancelado:
                job.mensaje = "Cancelado."
                job.terminado_en = time.time()
                job.estado = "cancelado"
            except Exception as e:                      # noqa: BLE001
                job.error = str(e) or e.__class__.__name__
                job.mensaje = "Hubo un error."
                # El traceback va al log del trabajo, no a la cara del usuario.
                job.log.append("TRACEBACK\n" + traceback.format_exc())
                job.terminado_en = time.time()
                job.estado = "error"

        threading.Thread(target=correr, name=f"job-{tipo}-{job.id}", daemon=True).start()
        self.limpiar()
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def limpiar(self):
        """Descarta trabajos terminados hace rato para no acumular memoria
        (los resultados incluyen el catálogo entero)."""
        ahora = time.time()
        with self._lock:
            viejos = [k for k, j in self._jobs.items()
                      if j.terminado_en and ahora - j.terminado_en > TTL_TERMINADO]
            for k in viejos:
                self._jobs.pop(k, None)

    def activos(self):
        with self._lock:
            return [j for j in self._jobs.values() if j.estado in ("pendiente", "corriendo")]

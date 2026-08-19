# -*- coding: utf-8 -*-
"""Test offline del backend de la app: trabajos y servidor HTTP.

Corré:  python test_app.py
Sale 0 si todo pasa, 1 si algo falla. No necesita claves ni internet: levanta el
servidor real en un puerto libre de localhost y le pega con urllib.

Cubre lo que sostiene la app:
  - trabajos en segundo plano: progreso, resultado, error, cancelación
  - el log del trabajo no crece sin límite
  - ruteo, 404 y errores de API con mensaje mostrable
  - que no se pueda leer nada fuera de app/web (path traversal)
  - el endpoint que recupera el catálogo tras recargar la página
  - la descarga del ZIP con sus cabeceras
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "app"))
sys.path.insert(0, HERE)



def _esperar(cond, segundos=30, paso=0.02):
    """Espera hasta que cond() sea verdadera. Por reloj y no por cantidad de
    vueltas: un runner de CI cargado puede tardar mucho mas que una maquina
    libre, y un test que se rinde por conteo se vuelve inestable justo en la
    puerta del build."""
    import time as _t
    limite = _t.monotonic() + segundos
    while _t.monotonic() < limite:
        if cond():
            return True
        _t.sleep(paso)
    return cond()


def main():
    import jobs as J
    import productos as P
    import server as backend

    fails = []

    def expect(name, got, want):
        if got != want:
            fails.append(f"  [{name}] got {got!r}, want {want!r}")

    def check(name, cond, detalle=""):
        if not cond:
            fails.append(f"  [{name}] falló {detalle}")

    # ========================================================
    # Trabajos en segundo plano
    # ========================================================
    reg = J.Registry()

    # --- caso feliz, con progreso ---
    def ok(job):
        job.avance("empezando", 0.1)
        job.avance("mitad", 0.5)
        return {"valor": 42}

    j = reg.lanzar("prueba", ok)
    _esperar(lambda: j.estado in ("listo", "error"))
    expect("job.estado_ok", j.estado, "listo")
    expect("job.resultado", j.resultado, {"valor": 42})
    expect("job.progreso_final", j.progreso, 1.0)
    check("job.log_acumula", "mitad" in j.log, f"log={j.log}")
    d = j.a_dict(con_log=True)
    expect("job.dict_tiene_resultado", d["resultado"], {"valor": 42})
    check("job.dict_tiene_log", "log" in d)

    # --- error: el mensaje llega, el traceback queda en el log ---
    def falla(job):
        raise RuntimeError("se rompió algo")

    j = reg.lanzar("prueba", falla)
    _esperar(lambda: j.estado in ("listo", "error"))
    expect("job.estado_error", j.estado, "error")
    expect("job.error_mensaje", j.error, "se rompió algo")
    check("job.traceback_al_log", any("TRACEBACK" in x for x in j.log))
    # El dict de un trabajo con error NO debe traer resultado.
    check("job.error_sin_resultado", "resultado" not in j.a_dict())

    # --- cancelación: avance() la detecta y corta ---
    arrancó = threading.Event()

    def largo(job):
        arrancó.set()
        for i in range(1000):
            job.avance(f"paso {i}", i / 1000)
            time.sleep(0.01)
        return "no deberia llegar"

    j = reg.lanzar("prueba", largo)
    check("job.cancelable_arranco", arrancó.wait(30))
    time.sleep(0.05)
    j.cancelar()
    _esperar(lambda: j.estado in ("cancelado", "listo", "error"))
    expect("job.cancelado", j.estado, "cancelado")
    check("job.cancelado_sin_resultado", j.resultado is None, f"resultado={j.resultado!r}")

    # --- el log no crece indefinidamente ---
    def charlatan(job):
        for i in range(J.MAX_LOG * 3):
            job.avance(f"linea {i}")
        return True

    j = reg.lanzar("prueba", charlatan)
    _esperar(lambda: j.estado in ("listo", "error"), segundos=60)
    expect("job.log_acotado_estado", j.estado, "listo")
    check("job.log_acotado", len(j.log) <= J.MAX_LOG, f"len={len(j.log)}")
    # Conserva el arranque y la cola.
    check("job.log_conserva_inicio", j.log[0] == "linea 0", f"primero={j.log[0]!r}")
    check("job.log_conserva_final", "linea " + str(J.MAX_LOG * 3 - 1) in j.log[-1])

    # --- inexistente ---
    check("job.get_inexistente", reg.get("nohay") is None)

    # ========================================================
    # Servidor HTTP
    # ========================================================
    def t(track, album, year, isrc, upc, vid, dist="ONErpm", dur=200):
        return {"video_id": vid, "track": track, "album": album, "distributor": dist,
                "category": "diy", "label": "Sello", "release_year": year, "isrc": isrc,
                "upc": upc, "match": "", "duration_s": dur, "views": 10, "likes": 0,
                "comments": 0, "upload_date": f"{year or 2020}-01-01", "desc3": "",
                "url": f"https://youtu.be/{vid}"}

    prods = P.group_products([
        t("Tema A", "Disco Uno", 2020, "ARABC2000001", "036000291452", "a1"),
        t("Tema B", "Disco Uno", 2020, "ARABC2000002", "036000291452", "a2"),
        t("Single", "(single / sin álbum)", 2021, "MALFORMADO", "", "b1", dist="DistroKid"),
    ], artist="Artista Test")
    backend.ESTADO.productos = prods
    backend.ESTADO.artista = "Artista Test"

    srv = backend.crear_servidor(0)
    puerto = srv.server_address[1]
    base = f"http://127.0.0.1:{puerto}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def _responde():
        try:
            urllib.request.urlopen(f"{base}/api/config", timeout=2).read()
            return True
        except Exception:
            return False

    check("http.arranco", _esperar(_responde, 30, 0.05), "el servidor no respondio")

    def get(ruta):
        # Reintento ante cortes de conexion: con keep-alive en Windows una
        # consulta suelta puede abortar (WinError 10053). Acá probamos la lógica
        # del backend, no la sincronización de sockets del sistema.
        for intento in range(4):
            try:
                with urllib.request.urlopen(f"{base}{ruta}", timeout=20) as r:
                    return r.status, r.read(), dict(r.headers)
            except urllib.error.HTTPError as e:
                return e.code, e.read(), dict(e.headers)
            except (ConnectionError, OSError):
                if intento == 3:
                    raise
                time.sleep(0.2)

    def post(ruta, cuerpo):
        req = urllib.request.Request(
            f"{base}{ruta}", data=json.dumps(cuerpo).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        for intento in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read())
                except ValueError:
                    return e.code, {}
            except (ConnectionError, OSError):
                if intento == 3:
                    raise
                time.sleep(0.2)

    try:
        # --- estáticos ---
        cod, cuerpo, hdr = get("/")
        expect("http.index", cod, 200)
        check("http.index_es_html", b"Migrador de Cat" in cuerpo)
        expect("http.css", get("/app.css")[0], 200)
        expect("http.js", get("/app.js")[0], 200)
        expect("http.token", get("/tokens/colors.css")[0], 200)
        expect("http.inexistente", get("/no-existe.js")[0], 404)

        # --- path traversal: nada fuera de app/web ---
        for intento in ("/../server.py", "/../../relevar_core.py", "/%2e%2e/server.py",
                        "/..%2f..%2fvalidar.py", "/web/../../server.py"):
            expect(f"http.traversal:{intento}", get(intento)[0], 404)

        # --- config ---
        cod, cuerpo, _ = get("/api/config")
        cfg = json.loads(cuerpo)
        expect("api.config", cod, 200)
        check("api.config_version", cfg.get("version") == backend.VERSION)
        check("api.config_catalogo_cargado", cfg.get("catalogo_cargado") is True)
        check("api.config_audio_apagado", cfg.get("audio_habilitado") is False,
              "el audio debe venir apagado por defecto")

        # --- catálogo (recupera tras recargar la página) ---
        cod, cuerpo, _ = get("/api/catalogo")
        cat = json.loads(cuerpo)
        expect("api.catalogo", cod, 200)
        expect("api.catalogo_artista", cat["artista"], "Artista Test")
        expect("api.catalogo_n", len(cat["productos"]), 2)
        check("api.catalogo_tiene_filtros", "distribuidoras" in cat["filtros"])
        check("api.catalogo_detalle", len(cat["productos"][0]["detalle"]) >= 1)

        # --- validar ---
        cod, res = post("/api/validar", {"ids": [p["product_id"] for p in prods]})
        expect("api.validar", cod, 200)
        check("api.validar_detecta_isrc", any(h["codigo"] == "isrc_invalido" for h in res["hallazgos"]),
              f"hallazgos={[h['codigo'] for h in res['hallazgos']]}")
        expect("api.validar_no_apto", res["apto"], False)

        # --- errores mostrables ---
        cod, res = post("/api/validar", {"ids": ["noexiste"]})
        expect("api.validar_vacio_codigo", cod, 400)
        check("api.validar_vacio_mensaje", "seleccionados" in res.get("error", ""))

        cod, res = post("/api/relevar", {"url": ""})
        expect("api.relevar_sin_url", cod, 400)
        check("api.relevar_mensaje", "link" in res.get("error", "").lower())

        expect("api.ruta_inexistente", post("/api/nada", {})[0], 404)

        cod, res = post("/api/preparar", {"ids": [prods[0]["product_id"]],
                                          "planilla": False, "portadas": False, "audio": False})
        expect("api.preparar_sin_nada", cod, 400)

        # El audio pedido con el módulo apagado no debe habilitarlo.
        check("api.audio_apagado_no_se_activa", backend.AUDIO_HABILITADO is False)

        # --- preparar de verdad (sin red: sólo planilla) ---
        cod, res = post("/api/preparar", {"ids": [p["product_id"] for p in prods],
                                          "planilla": True, "portadas": False, "audio": False})
        expect("api.preparar", cod, 200)
        job_id = res["job"]["id"]

        resultado = None
        import time as _t
        limite = _t.monotonic() + 120
        while _t.monotonic() < limite:
            cod, cuerpo, _ = get(f"/api/job/{job_id}")
            est = json.loads(cuerpo)
            if est["estado"] == "listo":
                resultado = est["resultado"]
                break
            if est["estado"] == "error":
                fails.append(f"  [api.preparar_job] error: {est.get('error')}")
                break
            _t.sleep(0.05)

        check("api.preparar_termino", resultado is not None)
        if resultado:
            check("api.preparar_bytes", resultado["bytes"] > 0)
            check("api.preparar_validacion", resultado["validacion"]["resumen"]["errores"] >= 1)

            cod, cuerpo, hdr = get(resultado["descarga"])
            expect("api.descarga", cod, 200)
            expect("api.descarga_tipo", hdr.get("Content-Type"), "application/zip")
            check("api.descarga_nombre", "attachment" in (hdr.get("Content-Disposition") or ""))
            expect("api.descarga_largo", int(hdr.get("Content-Length")), len(cuerpo))

            import io
            z = zipfile.ZipFile(io.BytesIO(cuerpo))
            check("api.zip_integro", z.testzip() is None)
            nombres = z.namelist()
            check("api.zip_validacion", any("Validacion" in n for n in nombres), f"{nombres}")
            check("api.zip_ingesta", any("ingesta" in n for n in nombres))
            check("api.zip_por_producto", any("Disco Uno" in n for n in nombres))
            # No pedimos portadas ni audio: no deben aparecer.
            check("api.zip_sin_portada", not any(n.endswith("portada.jpg") for n in nombres))

        # --- job inexistente ---
        expect("api.job_inexistente", get("/api/job/deadbeef")[0], 404)
        expect("api.descarga_inexistente", get("/api/descargar/deadbeef")[0], 404)

        # --- cancelar por HTTP ---
        cod, res = post(f"/api/job/{job_id}/cancelar", {})
        expect("api.cancelar_existente", cod, 200)
        expect("api.cancelar_inexistente", post("/api/job/deadbeef/cancelar", {})[0], 404)

        # --- body inválido ---
        req = urllib.request.Request(f"{base}/api/validar", data=b"{no es json}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            fails.append("  [api.body_invalido] debería fallar")
        except urllib.error.HTTPError as e:
            expect("api.body_invalido", e.code, 400)

    finally:
        srv.shutdown()
        srv.server_close()
        backend.ESTADO.limpiar()

    if fails:
        print("FALLARON:")
        print("\n".join(fails))
        return 1
    print("OK - backend de la app (trabajos + servidor)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

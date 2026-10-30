"""Fábrica 3D: subís fotos, la línea de montaje te devuelve un modelo para SketchUp.

Correr:  uvicorn app:app --reload   y abrir http://localhost:8000
"""
import asyncio
import base64
import os
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from fabrica import disenador, linea  # noqa: E402  (después de cargar el .env)

app = FastAPI(title="Fábrica 3D")


@app.middleware("http")
async def contrasena(request: Request, call_next):
    """Si FABRICA_CLAVE está definida, el navegador pide usuario (cualquiera) y esa contraseña."""
    clave = os.getenv("FABRICA_CLAVE")
    if clave:
        auth = request.headers.get("authorization", "")
        try:
            _, _, dada = base64.b64decode(auth.removeprefix("Basic ")).decode().partition(":")
        except ValueError:
            dada = ""
        if not secrets.compare_digest(dada.encode(), clave.encode()):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Fabrica 3D"'})
    return await call_next(request)


@app.on_event("startup")
async def reanudar():
    # Pedidos que quedaron a mitad de camino cuando se apagó el servidor.
    for p in linea.listar():
        if p["estado"] == "en_marcha":
            asyncio.create_task(linea.avanzar(p["id"]))


def _resumen(p: dict) -> dict:
    return {k: p[k] for k in ("id", "nombre", "linea", "creado", "estado", "paso", "error")} | {
        "miniatura": (p["renders"].get("final") or p["renders"].get("boceto") or [None])[0],
        "nota": (p.get("revision") or {}).get("nota"),
        "caras": (p.get("revision") or {}).get("total_caras"),
    }


@app.get("/api/estado")
def estado():
    pedidos = linea.listar()
    return {
        "ia": disenador.hay_ia(),
        "pasos": [{"id": a, "nombre": b, "detalle": c, "humano": d} for a, b, c, d in linea.PASOS],
        "pedidos": [_resumen(p) for p in pedidos],
        "previas": [_resumen(p) for p in pedidos if p["estado"] == "espera_vos"],
        "aprendizajes": linea.aprendizajes(),
        "plantillas": disenador.listar_plantillas(),
    }


@app.post("/api/pedidos")
async def nuevo_pedido(
    nombre: str = Form(...),
    fotos: list[UploadFile] = File(default=[]),
    notas: str = Form(""),
    medidas: str = Form(""),
    presupuesto: int = Form(0),
    plantilla: str = Form(""),
):
    archivos = [(f.filename or "foto.jpg", await f.read()) for f in fotos]
    archivos = [a for a in archivos if a[1]]
    if not archivos and not plantilla:
        raise HTTPException(400, "Subí al menos una foto, plano o boceto")
    datos = {"notas": notas, "medidas": medidas, "presupuesto": presupuesto or None, "plantilla": plantilla or None}
    p = linea.crear(nombre, archivos, datos)
    asyncio.create_task(linea.avanzar(p["id"]))
    return p


def _pedido(pid: str) -> dict:
    p = linea.cargar(pid)
    if not p:
        raise HTTPException(404, "No existe ese pedido")
    return p


@app.get("/api/pedidos/{pid}")
def ver_pedido(pid: str):
    p = _pedido(pid)
    return p | {"ficha": linea.ficha_actual(p) if p["ficha_version"] else None}


@app.post("/api/pedidos/{pid}/aprobar")
async def aprobar(pid: str):
    _pedido(pid)
    try:
        linea.aprobar(pid)
    except ValueError as e:
        raise HTTPException(409, str(e))
    asyncio.create_task(linea.avanzar(pid))
    return {"ok": True}


class Cambio(BaseModel):
    texto: str
    recordar: bool = True


@app.post("/api/pedidos/{pid}/cambio")
async def cambio(pid: str, c: Cambio):
    _pedido(pid)
    try:
        await asyncio.to_thread(linea.pedir_cambio, pid, c.texto, c.recordar)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e))
    asyncio.create_task(linea.avanzar(pid))
    return {"ok": True}


@app.put("/api/pedidos/{pid}/ficha")
async def ficha(pid: str, cuerpo: dict):
    _pedido(pid)
    try:
        linea.editar_ficha(pid, cuerpo)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"La ficha no arma: {e}")
    asyncio.create_task(linea.avanzar(pid))
    return {"ok": True}


@app.post("/api/pedidos/{pid}/reintentar")
async def reintentar(pid: str):
    _pedido(pid)
    asyncio.create_task(linea.avanzar(pid))
    return {"ok": True}


@app.get("/pedidos/{pid}/{ruta:path}")
def archivo(pid: str, ruta: str):
    base = linea.carpeta(pid).resolve()
    destino = (base / ruta).resolve()
    if base not in destino.parents or not destino.is_file() or destino.name == "pedido.json":
        raise HTTPException(404)
    return FileResponse(destino, filename=destino.name if ruta.startswith("entrega/") else None)


app.mount("/", StaticFiles(directory=linea.RAIZ / "static", html=True), name="static")

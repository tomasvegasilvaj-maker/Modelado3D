"""La línea de montaje: lleva un pedido por las etapas y frena donde decidís vos."""
from __future__ import annotations

import asyncio
import copy
import os
import uuid
from datetime import datetime
from pathlib import Path

from . import disenador, exportar, fotografo, geometria, revisor
from .util import guardar_json, leer_json

RAIZ = Path(__file__).resolve().parent.parent
PEDIDOS = RAIZ / "pedidos"
APRENDIZAJES = RAIZ / "aprendizajes.json"
MAX_VUELTAS = 3

PASOS = [
    ("intake", "Intake", "fotos y datos recibidos", False),
    ("diseno", "Diseño", "despiece y ficha con evidencia", False),
    ("boceto", "Boceto", "modelo gris liviano + fotos", False),
    ("ok_boceto", "Vos aprobás el boceto", "forma y proporciones", True),
    ("modelado", "Modelado", "geometría final por perfiles", False),
    ("revisor", "Revisor", f"criterios fijos, máx. {MAX_VUELTAS} vueltas", False),
    ("fotografo", "Fotógrafo", "6 vistas con cotas", False),
    ("ok_final", "Vos aprobás", "modelo final", True),
    ("entrega", "Entrega", "DAE, OBJ, STL, GLB y despiece", False),
]
IDX = {p[0]: i for i, p in enumerate(PASOS)}

_locks: dict[str, asyncio.Lock] = {}


def ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ persistencia
def carpeta(pid: str) -> Path:
    return PEDIDOS / pid


def cargar(pid: str) -> dict | None:
    return leer_json(carpeta(pid) / "pedido.json")


def guardar(p: dict) -> None:
    guardar_json(carpeta(p["id"]) / "pedido.json", p)


def listar() -> list[dict]:
    PEDIDOS.mkdir(exist_ok=True)
    pedidos = [leer_json(d / "pedido.json") for d in PEDIDOS.iterdir() if (d / "pedido.json").exists()]
    return sorted([p for p in pedidos if p], key=lambda p: p["creado"], reverse=True)


def ficha_actual(p: dict) -> dict:
    return leer_json(carpeta(p["id"]) / f"ficha_v{p['ficha_version']}.json")


def guardar_ficha(p: dict, ficha: dict, motivo: str) -> None:
    p["ficha_version"] += 1
    guardar_json(carpeta(p["id"]) / f"ficha_v{p['ficha_version']}.json", ficha)
    log(p, f"Ficha v{p['ficha_version']}: {motivo}")


def aprendizajes() -> list[dict]:
    return leer_json(APRENDIZAJES, [])


def aprender(texto: str, pedido: str) -> None:
    lista = aprendizajes()
    lista.append({"texto": texto, "pedido": pedido, "fecha": ahora()})
    guardar_json(APRENDIZAJES, lista)


def log(p: dict, texto: str) -> None:
    p["historial"].append({"t": ahora(), "texto": texto})


def _paso(p: dict, clave: str, estado: str = "now") -> None:
    p["paso"] = clave
    p["estado"] = "espera_vos" if PASOS[IDX[clave]][3] else "en_marcha"
    if estado == "listo":
        p["estado"] = "listo"


def _sumar_uso(p: dict, uso: dict) -> None:
    u = p["uso_ia"]
    u["entrada"] += uso.get("entrada", 0)
    u["salida"] += uso.get("salida", 0)
    precio_in = float(os.getenv("PRECIO_ENTRADA_MTOK", 3))
    precio_out = float(os.getenv("PRECIO_SALIDA_MTOK", 15))
    u["costo_usd"] = round(u["entrada"] / 1e6 * precio_in + u["salida"] / 1e6 * precio_out, 3)


def _tope_ok(p: dict) -> bool:
    return p["uso_ia"]["costo_usd"] < float(os.getenv("FABRICA_TOPE_USD", 2.5))


def _fotos_ref(p: dict) -> list[bytes]:
    return [(carpeta(p["id"]) / "fotos" / f).read_bytes() for f in p["fotos"]]


# --------------------------------------------------------------------- crear
def crear(nombre: str, fotos: list[tuple[str, bytes]], datos: dict) -> dict:
    pid = datetime.now().strftime("%m%d") + "-" + uuid.uuid4().hex[:5]
    c = carpeta(pid)
    (c / "fotos").mkdir(parents=True)
    nombres = []
    for i, (fn, data) in enumerate(fotos):
        ext = Path(fn).suffix.lower() or ".jpg"
        nombres.append(f"foto_{i + 1}{ext}")
        (c / "fotos" / nombres[-1]).write_bytes(data)
    p = {
        "id": pid, "nombre": nombre, "linea": "objetos", "creado": ahora(),
        "estado": "en_marcha", "paso": "intake", "datos": datos, "fotos": nombres,
        "ficha_version": 0, "vueltas": [], "revision": None, "renders": {},
        "archivos": {}, "uso_ia": {"entrada": 0, "salida": 0, "costo_usd": 0.0},
        "historial": [], "error": None,
    }
    log(p, f"Pedido recibido con {len(nombres)} foto(s)")
    guardar(p)
    return p


# ------------------------------------------------------------------ etapas
def _render(p: dict, ficha: dict, vistas: list[str], prefijo: str, boceto: bool) -> list[str]:
    piezas = geometria.construir(ficha, resolucion=0.6 if boceto else 1.0)
    despiece = geometria.construir(ficha, resolucion=0.6 if boceto else 1.0, despiece=1.0)
    cotas = None if boceto else revisor.medir_cotas(ficha, piezas)
    rutas = []
    for v in vistas:
        img = fotografo.fotografiar(piezas, v, ficha.get("ensamble"), cotas, boceto=boceto,
                                    piezas_despiece=despiece)
        nombre = f"render/{prefijo}_v{p['ficha_version']}_{v}.png"
        (carpeta(p["id"]) / "render").mkdir(exist_ok=True)
        img.save(carpeta(p["id"]) / nombre)
        rutas.append(nombre)
    return rutas


def _etapa_diseno(p: dict) -> None:
    datos = p["datos"]
    if datos.get("plantilla"):
        ficha = disenador.plantilla(datos["plantilla"])
        if datos.get("presupuesto"):
            ficha["presupuesto_caras"] = int(datos["presupuesto"])
        guardar_ficha(p, ficha, f"plantilla {datos['plantilla']}")
        return
    if not disenador.hay_ia():
        raise RuntimeError("Falta ANTHROPIC_API_KEY en el .env para diseñar desde fotos. "
                           "Mientras tanto podés usar una plantilla.")
    ficha, uso = disenador.disenar(_fotos_ref(p), datos, [a["texto"] for a in aprendizajes()])
    _sumar_uso(p, uso)
    guardar_ficha(p, ficha, "diseñada por IA a partir de las fotos")


def _etapa_revisor(p: dict) -> None:
    ficha = ficha_actual(p)
    for n in range(1, MAX_VUELTAS + 1):
        piezas = geometria.construir(ficha)
        rev = revisor.revisar(ficha, piezas)
        vuelta = {"n": n, "nota": rev["nota"], "total_caras": rev["total_caras"],
                  "fallas": [c["criterio"] + ": " + c["detalle"] for c in rev["criterios"] if c["estado"] == "falla"],
                  "cambios": []}
        if rev["aprobado"] and disenador.hay_ia() and _tope_ok(p) and not p["datos"].get("plantilla"):
            # Revisión visual contra las fotos (solo si hay IA y presupuesto).
            renders = [(carpeta(p["id"]) / r).read_bytes()
                       for r in _render(p, ficha, ["frente", "iso", "lateral"], f"revision{n}", False)]
            dif, uso = disenador.revisar_visual(_fotos_ref(p), renders, ficha)
            _sumar_uso(p, uso)
            graves = [d for d in dif if d.get("gravedad") in ("alta", "media")]
            if graves:
                rev["aprobado"] = False
                vuelta["fallas"] += [f"Visual · {d.get('pieza')}: {d.get('detalle')}" for d in graves]
        p["revision"] = {k: v for k, v in rev.items() if k != "cotas"}
        if rev["aprobado"]:
            p["vueltas"].append(vuelta)
            log(p, f"Revisor: aprobado en la vuelta {n} ({rev['total_caras']} caras)")
            break
        if rev["solo_corregibles"]:
            vuelta["cambios"] = revisor.ajustar_presupuesto(ficha, piezas)
            guardar_ficha(p, ficha, "el revisor bajó caras: " + "; ".join(vuelta["cambios"]))
        elif disenador.hay_ia() and _tope_ok(p) and n < MAX_VUELTAS:
            ficha, uso = disenador.disenar(_fotos_ref(p), p["datos"], [a["texto"] for a in aprendizajes()],
                                           ficha_previa=ficha, correcciones=vuelta["fallas"])
            _sumar_uso(p, uso)
            vuelta["cambios"] = ["el diseñador corrigió la ficha"]
            guardar_ficha(p, ficha, "corrección pedida por el revisor")
        else:
            p["vueltas"].append(vuelta)
            log(p, "Revisor: quedan observaciones para que decidas vos")
            break
        p["vueltas"].append(vuelta)
        guardar(p)


def _correr(p: dict) -> None:
    """Avanza etapas automáticas hasta un punto de decisión o el final."""
    while True:
        clave = p["paso"]
        if clave == "intake":
            _paso(p, "diseno")
        elif clave == "diseno":
            _etapa_diseno(p)
            _paso(p, "boceto")
        elif clave == "boceto":
            p["renders"]["boceto"] = _render(p, ficha_actual(p), ["frente", "iso", "despiece"], "boceto", True)
            log(p, "Boceto listo para que lo mires")
            _paso(p, "ok_boceto")
        elif clave == "modelado":
            geometria.construir(ficha_actual(p))  # falla temprano si la ficha no arma
            _paso(p, "revisor")
        elif clave == "revisor":
            _etapa_revisor(p)
            _paso(p, "fotografo")
        elif clave == "fotografo":
            ficha = ficha_actual(p)
            p["renders"]["final"] = _render(p, ficha, ["iso", "frente", "lateral", "superior", "despiece", "instalada"],
                                            "final", False)
            # GLB liviano para el visor 3D de la revisión final.
            vista_previa = f"render/preview_v{p['ficha_version']}.glb"
            escena = exportar.escena(geometria.construir(ficha), y_arriba_metros=True)
            escena.export(carpeta(p["id"]) / vista_previa, include_normals=True)
            p["renders"]["glb"] = vista_previa
            log(p, "Fotógrafo: 6 vistas listas")
            _paso(p, "ok_final")
        elif clave == "entrega":
            ficha = ficha_actual(p)
            piezas = geometria.construir(ficha)
            despiece = geometria.construir(ficha, despiece=1.0)
            p["archivos"] = {k: "entrega/" + v for k, v in
                             exportar.exportar_todo(ficha, piezas, despiece, carpeta(p["id"]) / "entrega").items()}
            p["ficha_tecnica"] = exportar.ficha_tecnica(piezas)
            log(p, "Entregado: archivos listos para SketchUp")
            _paso(p, "entrega", "listo")
            guardar(p)
            return
        guardar(p)
        if p["estado"] == "espera_vos":
            return


async def avanzar(pid: str) -> None:
    lock = _locks.setdefault(pid, asyncio.Lock())
    async with lock:
        p = cargar(pid)
        p["error"] = None
        if p["estado"] != "espera_vos":
            p["estado"] = "en_marcha"
        guardar(p)
        try:
            await asyncio.to_thread(_correr, p)
        except Exception as e:  # noqa: BLE001
            p["error"] = str(e)
            p["estado"] = "error"
            log(p, f"Error en {p['paso']}: {e}")
            guardar(p)


# ------------------------------------------------------------- tus decisiones
def aprobar(pid: str) -> dict:
    p = cargar(pid)
    if p["paso"] == "ok_boceto":
        log(p, "Aprobaste el boceto")
        _paso(p, "modelado")
    elif p["paso"] == "ok_final":
        log(p, "Aprobaste el modelo final")
        _paso(p, "entrega")
    else:
        raise ValueError("Este pedido no está esperando tu aprobación")
    guardar(p)
    return p


def pedir_cambio(pid: str, texto: str, recordar: bool) -> dict:
    """Vuelve al diseño con tu pedido. Si 'recordar', la fábrica lo usa en los próximos pedidos."""
    p = cargar(pid)
    if p["paso"] not in ("ok_boceto", "ok_final"):
        raise ValueError("Solo se piden cambios en un punto de revisión")
    if recordar:
        aprender(texto, p["nombre"])
    log(p, f"Pediste un cambio: {texto}")
    if not disenador.hay_ia():
        guardar(p)
        raise RuntimeError("Sin ANTHROPIC_API_KEY el cambio no se puede aplicar solo: "
                           "editá la ficha a mano (abajo, en 'Ficha de diseño').")
    ficha, uso = disenador.disenar(_fotos_ref(p), p["datos"], [a["texto"] for a in aprendizajes()],
                                   ficha_previa=ficha_actual(p), correcciones=[texto])
    _sumar_uso(p, uso)
    guardar_ficha(p, ficha, f"cambio pedido por vos: {texto}")
    p["vueltas"] = []
    _paso(p, "boceto")
    guardar(p)
    return p


def editar_ficha(pid: str, ficha: dict) -> dict:
    p = cargar(pid)
    geometria.construir(copy.deepcopy(ficha), resolucion=0.5)  # valida antes de guardar
    guardar_ficha(p, ficha, "editada a mano")
    p["vueltas"] = []
    _paso(p, "boceto")
    guardar(p)
    return p

"""El revisor: criterios fijos, medibles, iguales para todos los pedidos.

Lo que se puede corregir solo (exceso de caras) lo corrige; lo demás lo
devuelve al diseñador o a vos, con el detalle de qué falló.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from . import geometria
from .fotografo import contexto_puerta

TOL_REL, TOL_ABS = 0.04, 1.5  # medidas: ±4 % o ±1,5 mm, lo que sea mayor


def _union_bounds(piezas, ids):
    bs = [p["malla"].bounds for p in piezas if p["pieza"]["id"] in ids]
    return np.min([b[0] for b in bs], axis=0), np.max([b[1] for b in bs], axis=0)


def medir_cotas(ficha: dict, piezas: list[dict]) -> list[dict]:
    """Convierte las cotas de la ficha en segmentos medidos sobre el modelo real."""
    env = {k: v for k, v in (ficha.get("ensamble") or {}).items() if isinstance(v, (int, float))}
    eje_i = {"x": 0, "y": 1, "z": 2}
    salida = []
    for c in geometria.resolver(ficha.get("cotas") or [], env):
        if "piezas" in c:
            lo, hi = _union_bounds(piezas, set(c["piezas"]))
            k = eje_i[c["eje"]]
            a, b = lo.copy(), lo.copy()
            b[k] = hi[k]
        else:
            a, b = np.asarray(c["a"], float), np.asarray(c["b"], float)
        salida.append(c | {"a": a.tolist(), "b": b.tolist(), "valor": float(np.linalg.norm(b - a))})
    return salida


def _cerca(m, tol):
    return abs(m[0] - m[1]) <= max(tol[0] * m[1], tol[1])


def revisar(ficha: dict, piezas: list[dict]) -> dict:
    criterios = []

    def criterio(nombre, ok, detalle, corregible=False, aviso=False):
        estado = "ok" if ok else ("aviso" if aviso else "falla")
        criterios.append({"criterio": nombre, "estado": estado, "detalle": detalle, "corregible": corregible})

    # 1. Presupuesto de caras por pieza y total
    total = sum(len(p["malla"].faces) for p in piezas)
    tope = ficha.get("presupuesto_caras", 5000)
    excedidas = [(p["pieza"]["id"], len(p["malla"].faces), p["pieza"].get("caras_max"))
                 for p in piezas if p["pieza"].get("caras_max") and len(p["malla"].faces) > p["pieza"]["caras_max"]]
    criterio("Caras por pieza dentro del presupuesto", not excedidas,
             "todas dentro" if not excedidas else
             "; ".join(f"{i}: {n} de {m}" for i, n, m in excedidas), corregible=True)
    criterio("Total de caras", total <= tope, f"{total:,} de {tope:,}".replace(",", "."), corregible=True)

    # 2. Medidas de cada pieza (comparadas sin importar la orientación)
    malas = []
    for p in piezas:
        esperado = p["pieza"].get("medidas")
        if not esperado:
            continue
        real = sorted(p["malla_local"].extents)
        esp = sorted(float(v) for v in esperado)
        if not all(_cerca((r, e), (TOL_REL, TOL_ABS)) for r, e in zip(real, esp)):
            malas.append(f"{p['pieza']['id']}: {'×'.join(f'{v:.0f}' for v in real)} "
                         f"(pedido {'×'.join(f'{v:.0f}' for v in esp)})")
    criterio("Medidas de cada pieza", not malas, "coinciden con la ficha (±4 %)" if not malas else "; ".join(malas))

    # 3. Cotas generales (las que se dibujan en los planos)
    cotas = medir_cotas(ficha, piezas)
    fuera = [f"{c['nombre']}: {c['valor']:.1f} (pedido {float(c['medida']):.0f})" for c in cotas
             if "medida" in c and not _cerca((c["valor"], float(c["medida"])), (0.03, 1.0))]
    criterio("Cotas generales", not fuera,
             ", ".join(f"{c['nombre']} {c['valor']:.0f}" for c in cotas if "medida" in c) if not fuera else "; ".join(fuera))

    # 4. Malla sana
    rotas = [p["pieza"]["id"] for p in piezas
             if not p["malla"].is_watertight or p["malla"].volume <= 0]
    criterio("Mallas cerradas y sanas", not rotas, "todas cerradas, sin caras sueltas" if not rotas else ", ".join(rotas))

    # 5. Sin piezas flotantes: cada pieza toca otra pieza o la puerta/marco
    cajas = [(p["pieza"]["id"], p["malla"].bounds) for p in piezas]
    if (ficha.get("ensamble") or {}).get("contexto") == "puerta":
        cajas += [(f"_ctx{i}", m.bounds) for i, (m, _) in enumerate(contexto_puerta(ficha["ensamble"]))]
    sueltas = []
    for i, (pid, b) in enumerate(cajas):
        if pid.startswith("_ctx"):
            continue
        toca = any(np.all(b[0] - 1.5 <= o[1]) and np.all(o[0] <= b[1] + 1.5)
                   for j, (_, o) in enumerate(cajas) if j != i)
        if not toca:
            sueltas.append(pid)
    criterio("Sin piezas flotantes", not sueltas, "todo encastra" if not sueltas else ", ".join(sueltas))

    # 6. Nada inventado: cada pieza dice de qué foto sale
    sin_ev = [p["pieza"]["id"] for p in piezas if not str(p["pieza"].get("evidencia", "")).strip()]
    criterio("Nada inventado (cada pieza tiene evidencia)", not sin_ev,
             "las piezas citan su foto" if not sin_ev else "sin evidencia: " + ", ".join(sin_ev))

    # 7. Rango de espesor de puerta (herrajes)
    ens = ficha.get("ensamble") or {}
    if "espesor_puerta_min" in ens and "E" in ens:
        problemas = []
        for e in (ens["espesor_puerta_min"], ens["espesor_puerta_max"]):
            f2 = copy.deepcopy(ficha)
            f2["ensamble"]["E"] = e
            for p in geometria.construir(f2, resolucion=0.5):
                lado, b = p["pieza"].get("lado"), p["malla"].bounds
                if lado == "interior" and b[0][1] < e / 2 - 0.05:
                    problemas.append(f"{p['pieza']['id']} invade la puerta con {e} mm")
                if lado == "exterior" and b[1][1] > -e / 2 + 0.05:
                    problemas.append(f"{p['pieza']['id']} invade la puerta con {e} mm")
        criterio(f"Entra en puertas de {ens['espesor_puerta_min']} a {ens['espesor_puerta_max']} mm",
                 not problemas, "las rosetas apoyan en ambas caras" if not problemas else "; ".join(problemas))

    fallas = [c for c in criterios if c["estado"] == "falla"]
    nota = round(5 * sum(c["estado"] == "ok" for c in criterios) / len(criterios), 1)
    return {
        "aprobado": not fallas,
        "nota": nota,
        "total_caras": total,
        "criterios": criterios,
        "solo_corregibles": bool(fallas) and all(c["corregible"] for c in fallas),
        "cotas": cotas,
    }


_PARAMS_RES = ("segmentos", "muestras", "puntos_seccion")


def ajustar_presupuesto(ficha: dict, piezas: list[dict]) -> list[str]:
    """Baja la resolución de las piezas que se pasan de caras. Devuelve qué cambió."""
    por_id = {p["id"]: p for p in ficha["piezas"]}
    cambios, hechas = [], set()
    for p in piezas:
        pid, caras, tope = p["pieza"]["id"], len(p["malla"].faces), p["pieza"].get("caras_max")
        if not tope or caras <= tope:
            continue
        # Se edita la pieza original (si es copia, la base).
        fuente = por_id[pid]
        while "copia_de" in fuente and not any(k in fuente.get("geo", {}) for k in _PARAMS_RES):
            fuente = por_id[fuente["copia_de"]]
        if fuente["id"] in hechas:
            continue
        hechas.add(fuente["id"])
        geo = fuente["geo"]
        n = sum(1 for k in _PARAMS_RES if isinstance(geo.get(k), (int, float))) or 1
        factor = (tope / caras) ** (1 / n) * 0.97
        for k in _PARAMS_RES:
            if k in geo and isinstance(geo[k], (int, float)):
                nuevo = max(8, int(geo[k] * factor))
                if nuevo < geo[k]:
                    cambios.append(f"{fuente['id']}: {k} {geo[k]} → {nuevo} ({caras} caras, tope {tope})")
                    geo[k] = nuevo
    return cambios

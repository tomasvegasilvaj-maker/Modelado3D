"""Motor de modelado por perfiles.

Cada pieza se describe con pocos números (un perfil para tornear, un camino con
sección para barrer, o una caja) y se genera con una cantidad de caras
controlada. Nada de subdivisiones: la malla sale liviana desde el principio.

Convención (igual que SketchUp): milímetros, Z hacia arriba.
Para herrajes de puerta: eje de la manija = Y (interior hacia +Y), caras de la
puerta en y = ±E/2, canto de la puerta en x = +B, manija centrada en el origen.
"""
from __future__ import annotations

import ast
import copy
import math
import operator

import numpy as np
import trimesh

# ---------------------------------------------------------------- expresiones
# Las fichas pueden usar variables del ensamble, p. ej. "E/2+40" (E = espesor
# de puerta, B = backset). Se evalúan con un intérprete mínimo, sin eval().
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval(node, env):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in env:
        return float(env[node.id])
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand, env))
    raise ValueError(f"Expresión no permitida: {ast.dump(node)}")


def resolver(valor, env: dict):
    """Reemplaza recursivamente los textos tipo 'E/2+40' por su número."""
    if isinstance(valor, str):
        try:
            return _eval(ast.parse(valor, mode="eval"), env)
        except (SyntaxError, ValueError):
            return valor
    if isinstance(valor, list):
        return [resolver(v, env) for v in valor]
    if isinstance(valor, dict):
        return {k: resolver(v, env) for k, v in valor.items()}
    return valor


# ------------------------------------------------------------------ utilidades
_R = trimesh.transformations.rotation_matrix
# Rotación que lleva el eje Z local (eje del torno) al eje pedido.
_EJES = {
    "z": np.eye(4),
    "-z": _R(np.pi, [1, 0, 0]),
    "y": _R(-np.pi / 2, [1, 0, 0]),
    "-y": _R(np.pi / 2, [1, 0, 0]),
    "x": _R(np.pi / 2, [0, 1, 0]),
    "-x": _R(-np.pi / 2, [0, 1, 0]),
}


def _interp(tabla, t):
    """tabla = [[t0, v0], [t1, v1], ...] con t en 0..1."""
    tabla = np.asarray(tabla, float)
    return np.interp(t, tabla[:, 0], tabla[:, 1])


def _catmull_rom(puntos: np.ndarray, muestras: int) -> np.ndarray:
    """Curva suave que pasa por los puntos de control, remuestreada por longitud."""
    p = np.vstack([2 * puntos[0] - puntos[1], puntos, 2 * puntos[-1] - puntos[-2]])
    densa = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for t in np.linspace(0, 1, 24, endpoint=False):
            t2, t3 = t * t, t * t * t
            densa.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                                + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    densa.append(p[-2])
    densa = np.array(densa)
    largo = np.r_[0, np.cumsum(np.linalg.norm(np.diff(densa, axis=0), axis=1))]
    objetivo = np.linspace(0, largo[-1], muestras)
    return np.column_stack([np.interp(objetivo, largo, densa[:, k]) for k in range(3)])


def _anillos_a_malla(anillos: list[np.ndarray], tapa_inicio, tapa_fin) -> trimesh.Trimesh:
    """Une anillos de igual cantidad de puntos en un tubo; tapa = None | 'plana' | punto."""
    n = len(anillos[0])
    verts = np.vstack(anillos)
    caras = []
    for i in range(len(anillos) - 1):
        a, b = i * n, (i + 1) * n
        for j in range(n):
            k = (j + 1) % n
            caras += [[a + j, b + j, b + k], [a + j, b + k, a + k]]
    for tapa, idx, invertir in ((tapa_inicio, 0, True), (tapa_fin, len(anillos) - 1, False)):
        if tapa is None:
            continue
        centro = anillos[idx].mean(axis=0) if isinstance(tapa, str) else np.asarray(tapa, float)
        c = len(verts)
        verts = np.vstack([verts, centro])
        base = idx * n
        for j in range(n):
            k = (j + 1) % n
            caras.append([c, base + k, base + j] if invertir else [c, base + j, base + k])
    m = trimesh.Trimesh(verts, np.array(caras), process=True)
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(m)
    return m


# ---------------------------------------------------------------- primitivas
def torno(perfil, segmentos: int = 24, eje: str = "z", **_) -> trimesh.Trimesh:
    """Gira un perfil [[radio, altura], ...] alrededor del eje (como el Screw de Blender)."""
    perfil = np.asarray(perfil, float)
    ang = np.linspace(0, 2 * np.pi, int(segmentos), endpoint=False)
    anillos, puntas = [], []
    for r, h in perfil:
        if r < 1e-6:
            puntas.append(np.array([0, 0, h]))
        else:
            anillos.append(np.column_stack([r * np.cos(ang), r * np.sin(ang), np.full_like(ang, h)]))
    # Los extremos con radio 0 cierran el sólido en punta (tapa en abanico).
    inicio = puntas[0] if perfil[0, 0] < 1e-6 else "plana"
    fin = puntas[-1] if perfil[-1, 0] < 1e-6 else "plana"
    m = _anillos_a_malla(anillos, inicio, fin)
    m.apply_transform(_rot_eje(eje))
    trimesh.repair.fix_normals(m)
    return m


def barrido(camino, alto, grosor, muestras: int = 24, puntos_seccion: int = 16,
            exponente: float = 2.4, arriba=(0, 0, 1), punta_redonda: bool = True,
            anillos_punta: int = 3, **_) -> trimesh.Trimesh:
    """Recorre una sección redondeada a lo largo de un camino suave.

    alto  = medida de la sección en la dirección 'arriba' (vertical instalada).
    grosor = medida en la otra dirección. Ambas son tablas [[t, mm], ...].
    """
    c = _catmull_rom(np.asarray(camino, float), int(muestras))
    t = np.linspace(0, 1, len(c))
    tang = np.gradient(c, axis=0)
    tang /= np.linalg.norm(tang, axis=1, keepdims=True)
    up = np.asarray(arriba, float)
    th = np.linspace(0, 2 * np.pi, int(puntos_seccion), endpoint=False)
    e = 2.0 / exponente  # superelipse: e < 1 da sección más "cuadrada"
    su = np.sign(np.sin(th)) * np.abs(np.sin(th)) ** e
    sv = np.sign(np.cos(th)) * np.abs(np.cos(th)) ** e
    a, g = _interp(alto, t), _interp(grosor, t)

    anillos = []
    for i in range(len(c)):
        n = up - up.dot(tang[i]) * tang[i]
        n /= np.linalg.norm(n)
        b = np.cross(tang[i], n)
        anillos.append(c[i] + np.outer(su * a[i] / 2, n) + np.outer(sv * g[i] / 2, b))

    tapa_fin = "plana"
    if punta_redonda:
        # Media cúpula al final: la punta de la palanca queda redonda, no cortada.
        r = min(a[-1], g[-1]) / 2
        n = up - up.dot(tang[-1]) * tang[-1]
        n /= np.linalg.norm(n)
        b = np.cross(tang[-1], n)
        for k in range(1, int(anillos_punta) + 1):
            phi = k / (anillos_punta + 1) * np.pi / 2
            s = math.cos(phi)
            centro = c[-1] + tang[-1] * r * math.sin(phi)
            anillos.append(centro + np.outer(su * a[-1] / 2 * s, n) + np.outer(sv * g[-1] / 2 * s, b))
        tapa_fin = c[-1] + tang[-1] * r
    return _anillos_a_malla(anillos, "plana", tapa_fin)


def caja(medidas, **_) -> trimesh.Trimesh:
    return trimesh.creation.box(extents=np.asarray(medidas, float))


def marco_rect(medidas, hueco, **_) -> trimesh.Trimesh:
    """Placa con una ventana rectangular (p. ej. contrachapa), sin booleanas.

    medidas = [espesor_x, ancho_y, alto_z]; hueco = [ancho_y, alto_z] centrado.
    """
    ex, ay, az = medidas
    hy, hz = hueco
    tiras = [
        ([ex, ay, (az - hz) / 2], [0, 0, (az + hz) / 4]),
        ([ex, ay, (az - hz) / 2], [0, 0, -(az + hz) / 4]),
        ([ex, (ay - hy) / 2, hz], [0, (ay + hy) / 4, 0]),
        ([ex, (ay - hy) / 2, hz], [0, -(ay + hy) / 4, 0]),
    ]
    partes = []
    for ext, pos in tiras:
        b = trimesh.creation.box(extents=ext)
        b.apply_translation(pos)
        partes.append(b)
    return trimesh.util.concatenate(partes)


TIPOS = {"torno": torno, "barrido": barrido, "caja": caja, "marco": marco_rect}


def _rot_eje(eje: str) -> np.ndarray:
    return _EJES[eje]


def _colocacion(colocar: dict, extra=(0, 0, 0)) -> np.ndarray:
    rx, ry, rz = np.radians(colocar.get("rot", [0, 0, 0]))
    m = trimesh.transformations.euler_matrix(rx, ry, rz, "sxyz")
    if colocar.get("espejo"):
        m = m @ np.diag([1, -1, 1, 1])  # espejo en Y: lado exterior de la puerta
    m[:3, 3] = np.asarray(colocar.get("pos", [0, 0, 0]), float) + np.asarray(extra, float)
    return m


# ------------------------------------------------------------------ ensamble
def piezas_resueltas(ficha: dict) -> list[dict]:
    """Aplica variables del ensamble y resuelve las piezas 'copia_de'."""
    env = {k: v for k, v in (ficha.get("ensamble") or {}).items() if isinstance(v, (int, float))}
    crudas = {p["id"]: p for p in ficha["piezas"]}

    def completa(p, vistos=()):
        if "copia_de" not in p:
            return copy.deepcopy(p)
        if p["copia_de"] in vistos or p["copia_de"] not in crudas:
            raise ValueError(f"copia_de inválido en {p.get('id')}")
        base = completa(crudas[p["copia_de"]], vistos + (p["id"],))
        geo = base.get("geo", {}) | p.get("geo", {})
        base.update({k: copy.deepcopy(v) for k, v in p.items() if k != "copia_de"})
        base["geo"] = geo
        return base

    return [resolver(completa(p), env) for p in ficha["piezas"]]


def construir_pieza(p: dict, resolucion: float = 1.0) -> trimesh.Trimesh:
    """Malla de una pieza en su marco local. resolucion < 1 = boceto más liviano."""
    geo = dict(p["geo"])
    if resolucion != 1.0:
        for k, minimo in (("segmentos", 8), ("muestras", 8), ("puntos_seccion", 8)):
            if k in geo:
                geo[k] = max(minimo, int(round(geo[k] * resolucion)))
    return TIPOS[p["tipo"]](**geo)


def construir(ficha: dict, resolucion: float = 1.0, despiece: float = 0.0) -> list[dict]:
    """Devuelve [{pieza, malla_local, malla}] con cada pieza ya colocada.

    despiece = 0 (armado) .. 1 (despiece completo, usa el campo 'despiece').
    """
    out = []
    for p in piezas_resueltas(ficha):
        local = construir_pieza(p, resolucion)
        extra = np.asarray(p.get("despiece", [0, 0, 0]), float) * despiece
        tf = _colocacion(p.get("colocar", {}), extra)
        m = local.copy()
        m.apply_transform(tf)  # trimesh ya corrige el sentido de las caras en el espejo
        out.append({"pieza": p, "malla_local": local, "malla": m})
    return out

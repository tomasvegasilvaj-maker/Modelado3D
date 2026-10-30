"""El fotógrafo: renders de cada etapa sin GPU ni Blender.

Rasterizador por software (numpy) con sombreado diferido: buffer de
profundidad + normales + id de pieza, y después luz de estudio con un reflejo
metálico satinado. Alcanza y sobra para mallas de pocos miles de caras.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont

MATERIALES = {
    # color base, intensidad especular, brillo, cuánto refleja el entorno
    "niquel_satinado": ((0.74, 0.75, 0.77), 0.55, 38, 0.38),
    "cromo": ((0.80, 0.81, 0.83), 0.9, 90, 0.6),
    "laton": ((0.78, 0.62, 0.32), 0.6, 45, 0.35),
    "acero": ((0.58, 0.60, 0.62), 0.35, 25, 0.2),
    "negro": ((0.12, 0.12, 0.13), 0.4, 40, 0.1),
    "madera": ((0.71, 0.55, 0.38), 0.08, 10, 0.0),
    "madera_clara": ((0.83, 0.71, 0.55), 0.08, 10, 0.0),
    "boceto": ((0.80, 0.81, 0.83), 0.08, 12, 0.0),
}

VISTAS = {
    "frente": {"dir": (0, -1, 0), "up": (0, 0, 1), "orto": True, "titulo": "Frente (lado interior)"},
    "lateral": {"dir": (-1, 0, 0), "up": (0, 0, 1), "orto": True, "titulo": "Lateral (desde el canto)"},
    "superior": {"dir": (0, 0, -1), "up": (0, 1, 0), "orto": True, "titulo": "Superior"},
    "iso": {"dir": (0.55, -0.85, -0.42), "up": (0, 0, 1), "orto": False, "titulo": "Isométrica"},
    "despiece": {"dir": (0.75, -0.62, -0.38), "up": (0, 0, 1), "orto": False, "despiece": 1.0,
                 "titulo": "Despiece técnico"},
    "instalada": {"dir": (0.5, -0.86, -0.18), "up": (0, 0, 1), "orto": False, "contexto": True,
                  "titulo": "Instalada en puerta"},
}


def _fuente(tam):
    for nombre in ("DejaVuSans.ttf", "Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def contexto_puerta(ensamble: dict) -> list[tuple[trimesh.Trimesh, str]]:
    """Puerta y marco para mostrar escala. No se exportan."""
    E, B = float(ensamble.get("E", 40)), float(ensamble.get("B", 60))
    hoja = trimesh.creation.box(extents=[B + 420, E, 620])
    hoja.apply_translation([(B - 420) / 2, 0, -60])
    marco = trimesh.creation.box(extents=[70, E + 30, 620])
    marco.apply_translation([B + 4.5 + 35, 0, -60])
    return [(hoja, "madera_clara"), (marco, "madera")]


class Camara:
    def __init__(self, direccion, arriba, orto, puntos, ancho, alto, margen=0.12, fov=28):
        d = np.asarray(direccion, float)
        self.d = d / np.linalg.norm(d)
        r = np.cross(self.d, arriba)
        if np.linalg.norm(r) < 1e-6:
            r = np.cross(self.d, (0, 1, 0))
        self.r = r / np.linalg.norm(r)
        self.u = np.cross(self.r, self.d)
        self.orto, self.W, self.H = orto, ancho, alto
        centro = (puntos.min(0) + puntos.max(0)) / 2
        radio = np.linalg.norm(puntos - centro, axis=1).max()
        if orto:
            x, y = (puntos - centro) @ self.r, (puntos - centro) @ self.u
            self.esc = min(ancho * (1 - 2 * margen) / max(np.ptp(x), 1e-6),
                           alto * (1 - 2 * margen) / max(np.ptp(y), 1e-6))
            self.centro2 = np.array([(x.max() + x.min()) / 2, (y.max() + y.min()) / 2])
            self.ojo = centro - self.d * radio * 4
        else:
            self.f = (alto / 2) / math.tan(math.radians(fov) / 2)
            self.ojo = centro - self.d * radio / math.sin(math.radians(fov) / 2) * 0.92
            # Recentrar con la proyección real para que el objeto quede al medio.
            sx, sy, _ = self._proy(puntos, recentrar=False)
            self.off = np.array([(sx.max() + sx.min()) / 2 - ancho / 2, (sy.max() + sy.min()) / 2 - alto / 2])
            esc = min(ancho * (1 - 2 * margen) / np.ptp(sx), alto * (1 - 2 * margen) / np.ptp(sy))
            self.f *= esc
            sx, sy, _ = self._proy(puntos, recentrar=False)
            self.off = np.array([(sx.max() + sx.min()) / 2 - ancho / 2, (sy.max() + sy.min()) / 2 - alto / 2])
        self.centro = centro

    def _proy(self, p, recentrar=True):
        q = np.asarray(p, float) - self.ojo
        z = q @ self.d
        if self.orto:
            x = (q @ self.r - self.centro2[0]) * self.esc + self.W / 2
            y = -(q @ self.u - self.centro2[1]) * self.esc + self.H / 2
            return x, y, z
        x = (q @ self.r) / z * self.f + self.W / 2
        y = -(q @ self.u) / z * self.f + self.H / 2
        if recentrar:
            x, y = x - self.off[0], y - self.off[1]
        return x, y, z

    def proyectar(self, p):
        return self._proy(p)


def _rasterizar(cam, mallas, W, H):
    zbuf = np.full((H, W), np.inf)
    nbuf = np.zeros((H, W, 3))
    ibuf = np.full((H, W), -1, int)
    for idx, malla in enumerate(mallas):
        lisa = malla.smooth_shaded  # separa vértices en aristas vivas (> ~30°)
        V, F, N = lisa.vertices, lisa.faces, lisa.vertex_normals
        sx, sy, sz = cam.proyectar(V)
        for f in F:
            x, y, z = sx[f], sy[f], sz[f]
            if (z <= 1e-6).any():
                continue
            x0, x1 = max(int(x.min()), 0), min(int(math.ceil(x.max())), W - 1)
            y0, y1 = max(int(y.min()), 0), min(int(math.ceil(y.max())), H - 1)
            if x0 > x1 or y0 > y1:
                continue
            area = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
            if abs(area) < 1e-9:
                continue
            px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            w0 = ((x[1] - px) * (y[2] - py) - (x[2] - px) * (y[1] - py)) / area
            w1 = ((x[2] - px) * (y[0] - py) - (x[0] - px) * (y[2] - py)) / area
            w2 = 1 - w0 - w1
            dentro = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not dentro.any():
                continue
            prof = w0 * z[0] + w1 * z[1] + w2 * z[2]
            zona = zbuf[y0:y1 + 1, x0:x1 + 1]
            gana = dentro & (prof < zona)
            if not gana.any():
                continue
            zona[gana] = prof[gana]
            n = w0[..., None] * N[f[0]] + w1[..., None] * N[f[1]] + w2[..., None] * N[f[2]]
            nbuf[y0:y1 + 1, x0:x1 + 1][gana] = n[gana]
            ibuf[y0:y1 + 1, x0:x1 + 1][gana] = idx
    return zbuf, nbuf, ibuf


def _sombrear(cam, zbuf, nbuf, ibuf, mats, W, H):
    fondo_arr = np.linspace(0.955, 0.86, H)[:, None, None] * np.ones((1, W, 3))
    img = fondo_arr.copy()
    hay = ibuf >= 0
    n = nbuf[hay]
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
    v = -cam.d
    # Normales que miran para atrás (mallas abiertas o espejadas) se dan vuelta.
    n *= np.where((n @ v) < 0, -1.0, 1.0)[:, None]

    luces = [  # dirección hacia la luz (mundo), intensidad
        (np.array([-0.4, 0.6, 0.9]), 0.75),   # principal, arriba
        (np.array([0.8, 0.5, 0.3]), 0.30),    # relleno
        (np.array([0.1, -0.9, 0.4]), 0.20),   # contraluz
    ]
    cuerpo = np.zeros_like(n)
    base = np.array([MATERIALES[mats[i]][0] for i in ibuf[hay]])
    ks = np.array([MATERIALES[mats[i]][1] for i in ibuf[hay]])[:, None]
    brillo = np.array([MATERIALES[mats[i]][2] for i in ibuf[hay]])[:, None]
    refl = np.array([MATERIALES[mats[i]][3] for i in ibuf[hay]])[:, None]

    amb = 0.32 + 0.12 * n[:, 2:3]
    cuerpo += base * amb
    for l, inten in luces:
        l = l / np.linalg.norm(l)
        dif = np.clip(n @ l, 0, None)[:, None]
        h = (l + v) / np.linalg.norm(l + v)
        esp = np.clip(n @ h, 0, None)[:, None] ** brillo
        cuerpo += base * dif * inten + ks * esp * inten
    # Reflejo de un estudio: techo claro, piso más oscuro, horizonte suave.
    r = 2 * (n @ v)[:, None] * n - v
    entorno = 0.45 + 0.5 * np.clip(r[:, 2:3], -1, 1) + 0.15 * np.exp(-(r[:, 2:3] * 6) ** 2)
    cuerpo = cuerpo * (1 - refl) + entorno * refl * (base * 0.6 + 0.4)
    img[hay] = np.clip(cuerpo, 0, 1)

    # Contorno: cambio de pieza o salto de profundidad.
    borde = np.zeros((H, W), bool)
    zf = np.where(hay, zbuf, 1e9)
    for dy, dx in ((0, 1), (1, 0)):
        a, b = ibuf, np.roll(ibuf, (-dy, -dx), (0, 1))
        za, zb = zf, np.roll(zf, (-dy, -dx), (0, 1))
        salto = np.abs(za - zb) > (np.minimum(za, zb) * 0.004 + 0.6)
        borde |= (a != b) | (salto & hay)
    img[borde] *= 0.45
    return img


def _cotas(img: Image.Image, cam, cotas, ss):
    d = ImageDraw.Draw(img)
    fuente = _fuente(13 * ss)
    color = (40, 60, 120)
    pila = {"h": 0, "v": 0}
    for c in cotas:
        a, b = np.asarray(c["a"], float), np.asarray(c["b"], float)
        ax, ay, _ = cam.proyectar(a[None])
        bx, by, _ = cam.proyectar(b[None])
        ax, ay, bx, by = float(ax[0]), float(ay[0]), float(bx[0]), float(by[0])
        texto = f"{c['nombre']}  {c['valor']:.0f} mm"
        if abs(bx - ax) >= abs(by - ay):
            y = c["borde"][3] + (26 + 30 * pila["h"]) * ss
            pila["h"] += 1
            for x0, y0 in ((ax, ay), (bx, by)):
                d.line([(x0, y0 + 6 * ss), (x0, y + 6 * ss)], fill=color + (120,), width=ss)
            d.line([(ax, y), (bx, y)], fill=color, width=2 * ss)
            for x0, s in ((ax, 1), (bx, -1)):
                d.polygon([(x0, y), (x0 + s * 9 * ss, y - 4 * ss), (x0 + s * 9 * ss, y + 4 * ss)], fill=color)
            tw = d.textlength(texto, font=fuente)
            d.text(((ax + bx) / 2 - tw / 2, y - 18 * ss), texto, fill=color, font=fuente)
        else:
            x = c["borde"][2] + (26 + 30 * pila["v"]) * ss
            pila["v"] += 1
            for x0, y0 in ((ax, ay), (bx, by)):
                d.line([(x0 + 6 * ss, y0), (x + 6 * ss, y0)], fill=color + (120,), width=ss)
            d.line([(x, ay), (x, by)], fill=color, width=2 * ss)
            for y0, s in ((ay, 1), (by, -1)):
                d.polygon([(x, y0), (x - 4 * ss, y0 + s * 9 * ss), (x + 4 * ss, y0 + s * 9 * ss)], fill=color)
            d.text((x + 8 * ss, (ay + by) / 2 - 8 * ss), texto, fill=color, font=fuente)


def fotografiar(piezas: list[dict], vista: str, ensamble: dict | None = None, cotas=None,
                ancho=1000, alto=750, ss=2, boceto=False, piezas_despiece=None) -> Image.Image:
    """piezas = salida de geometria.construir(); piezas_despiece = idem con despiece=1."""
    cfg = VISTAS[vista]
    lista = piezas_despiece if cfg.get("despiece") and piezas_despiece else piezas
    mallas = [p["malla"] for p in lista]
    mats = ["boceto" if boceto else p["pieza"].get("material", "niquel_satinado") for p in lista]
    foco = np.vstack([m.vertices for m in mallas])
    if cfg.get("contexto"):
        for m, mat in contexto_puerta(ensamble or {}):
            mallas.append(m)
            mats.append(mat)

    W, H = ancho * ss, alto * ss
    # Las vistas técnicas dejan lugar abajo y a la derecha para las cotas.
    margen = 0.2 if (cfg["orto"] and cotas) else 0.1
    cam = Camara(cfg["dir"], cfg["up"], cfg["orto"], foco, W, H, margen=margen)
    zbuf, nbuf, ibuf = _rasterizar(cam, mallas, W, H)
    arr = _sombrear(cam, zbuf, nbuf, ibuf, mats, W, H)
    img = Image.fromarray((arr * 255).astype(np.uint8))

    if cotas:
        sx, sy, _ = cam.proyectar(foco)
        borde = (sx.min(), sy.min(), sx.max(), sy.max())
        _cotas(img, cam, [c | {"borde": borde} for c in cotas if c["vista"] == vista], ss)

    img = img.resize((ancho, alto), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    d.text((22, 16), cfg["titulo"] + ("  ·  boceto" if boceto else ""), fill=(70, 74, 82), font=_fuente(26))
    return img

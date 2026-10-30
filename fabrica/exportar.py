"""Entrega: archivos para SketchUp/Blender y la ficha técnica.

El DAE se escribe a mano para que cada pieza llegue a SketchUp como un grupo
separado y con su nombre (trimesh exporta todo como una sola malla).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import trimesh

from .fotografo import MATERIALES


def _id(texto: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in texto)


def exportar_dae(piezas: list[dict], ruta: Path) -> None:
    mats = sorted({p["pieza"].get("material", "niquel_satinado") for p in piezas})
    efectos, materiales, geometrias, nodos = [], [], [], []
    for m in mats:
        r, g, b = MATERIALES.get(m, MATERIALES["niquel_satinado"])[0]
        efectos.append(
            f'<effect id="{m}-fx"><profile_COMMON><technique sid="common"><lambert>'
            f"<diffuse><color>{r:.3f} {g:.3f} {b:.3f} 1</color></diffuse>"
            f"</lambert></technique></profile_COMMON></effect>")
        materiales.append(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>')
    for p in piezas:
        pid = _id(p["pieza"]["id"])
        nombre = escape(p["pieza"].get("nombre", pid))
        mat = p["pieza"].get("material", "niquel_satinado")
        malla = p["malla"]
        v = " ".join(f"{x:.4f}" for x in malla.vertices.ravel())
        n = " ".join(f"{x:.4f}" for x in malla.face_normals.ravel())
        idx = np.column_stack([malla.faces[:, 0], np.arange(len(malla.faces)),
                               malla.faces[:, 1], np.arange(len(malla.faces)),
                               malla.faces[:, 2], np.arange(len(malla.faces))])
        geometrias.append(
            f'<geometry id="{pid}-geo" name="{nombre}"><mesh>'
            f'<source id="{pid}-pos"><float_array id="{pid}-pos-a" count="{malla.vertices.size}">{v}</float_array>'
            f'<technique_common><accessor source="#{pid}-pos-a" count="{len(malla.vertices)}" stride="3">'
            f'<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>'
            f"</accessor></technique_common></source>"
            f'<source id="{pid}-nor"><float_array id="{pid}-nor-a" count="{malla.face_normals.size}">{n}</float_array>'
            f'<technique_common><accessor source="#{pid}-nor-a" count="{len(malla.faces)}" stride="3">'
            f'<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>'
            f"</accessor></technique_common></source>"
            f'<vertices id="{pid}-vtx"><input semantic="POSITION" source="#{pid}-pos"/></vertices>'
            f'<triangles material="{mat}" count="{len(malla.faces)}">'
            f'<input semantic="VERTEX" source="#{pid}-vtx" offset="0"/>'
            f'<input semantic="NORMAL" source="#{pid}-nor" offset="1"/>'
            f'<p>{" ".join(map(str, idx.ravel()))}</p></triangles></mesh></geometry>')
        nodos.append(
            f'<node id="{pid}" name="{nombre}"><instance_geometry url="#{pid}-geo"><bind_material>'
            f'<technique_common><instance_material symbol="{mat}" target="#{mat}"/></technique_common>'
            f"</bind_material></instance_geometry></node>")
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">'
        f"<asset><contributor><authoring_tool>Fabrica 3D</authoring_tool></contributor>"
        f"<created>{ahora}</created><modified>{ahora}</modified>"
        '<unit meter="0.001" name="millimeter"/><up_axis>Z_UP</up_axis></asset>'
        f"<library_effects>{''.join(efectos)}</library_effects>"
        f"<library_materials>{''.join(materiales)}</library_materials>"
        f"<library_geometries>{''.join(geometrias)}</library_geometries>"
        f'<library_visual_scenes><visual_scene id="escena">{"".join(nodos)}</visual_scene></library_visual_scenes>'
        '<scene><instance_visual_scene url="#escena"/></scene></COLLADA>')
    Path(ruta).write_text(xml, encoding="utf-8")


def escena(piezas, y_arriba_metros=False) -> trimesh.Scene:
    escena = trimesh.Scene()
    for p in piezas:
        m = p["malla"].copy()
        if y_arriba_metros:  # glTF: metros e Y arriba, normales partidas en aristas vivas
            m = m.smooth_shaded
            m.apply_scale(0.001)
            m.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
        color = (np.array(MATERIALES.get(p["pieza"].get("material"), MATERIALES["niquel_satinado"])[0]) * 255)
        m.visual = trimesh.visual.ColorVisuals(m, face_colors=np.r_[color, 255].astype(np.uint8))
        escena.add_geometry(m, node_name=p["pieza"]["id"], geom_name=p["pieza"]["id"])
    return escena


def exportar_todo(ficha: dict, piezas: list[dict], piezas_despiece: list[dict], carpeta: Path) -> dict:
    carpeta.mkdir(parents=True, exist_ok=True)
    exportar_dae(piezas, carpeta / "modelo.dae")
    exportar_dae(piezas_despiece, carpeta / "modelo_despiece.dae")
    escena(piezas).export(carpeta / "modelo.obj")
    trimesh.util.concatenate([p["malla"] for p in piezas]).export(carpeta / "modelo.stl")
    escena(piezas, y_arriba_metros=True).export(carpeta / "modelo.glb", include_normals=True)
    escena(piezas_despiece, y_arriba_metros=True).export(carpeta / "modelo_despiece.glb", include_normals=True)
    filas = ficha_tecnica(piezas)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(filas[0].keys()))
    w.writeheader()
    w.writerows(filas)
    (carpeta / "despiece.csv").write_text(buf.getvalue(), encoding="utf-8")
    return {
        "dae": "modelo.dae", "dae_despiece": "modelo_despiece.dae", "obj": "modelo.obj",
        "stl": "modelo.stl", "glb": "modelo.glb", "glb_despiece": "modelo_despiece.glb",
        "csv": "despiece.csv",
    }


def ficha_tecnica(piezas: list[dict]) -> list[dict]:
    filas = []
    for p in piezas:
        ext = sorted(p["malla_local"].extents, reverse=True)
        filas.append({
            "id": p["pieza"]["id"],
            "pieza": p["pieza"].get("nombre", p["pieza"]["id"]),
            "lado": p["pieza"].get("lado", ""),
            "material": p["pieza"].get("material", ""),
            "medidas_mm": " × ".join(f"{v:.1f}" for v in ext),
            "caras": len(p["malla"].faces),
            "caras_max": p["pieza"].get("caras_max", ""),
            "volumen_cm3": round(abs(p["malla"].volume) / 1000, 2),
            "evidencia": p["pieza"].get("evidencia", ""),
        })
    return filas

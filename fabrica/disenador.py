"""El diseñador: mira las fotos y escribe la ficha de la pieza (sin modelar nada).

La IA no genera polígonos: devuelve una ficha con perfiles, caminos y medidas.
La geometría la arma el motor, así el resultado es fiel, liviano y editable.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path

from PIL import Image

from .util import json_compacto

PLANTILLAS = Path(__file__).parent / "plantillas"

FORMATO = """
FORMATO DE LA FICHA (JSON). Milímetros, Z hacia arriba (como SketchUp).
- nombre, categoria ("herraje" | "mueble" | "objeto"), descripcion
- presupuesto_caras: tope total de triángulos (herraje chico ~4500, mueble ~8000)
- ensamble: variables numéricas usables en cualquier campo como texto, p. ej. "E/2+42".
  Para herrajes de puerta: E = espesor de puerta, B = backset, y "contexto": "puerta".
  Convención herrajes: eje de la manija = Y (interior hacia +Y), caras de la puerta en y=±E/2,
  canto de la puerta en x=+B, manija centrada en el origen.
- cotas: [{vista: "frente"|"lateral"|"superior", nombre, piezas:[ids], eje:"x"|"y"|"z", medida?}]
         o [{vista, nombre, a:[x,y,z], b:[x,y,z], medida?}]. "medida" = valor que el revisor exige.
- piezas: lista. Cada pieza:
  id, nombre, lado, material ("niquel_satinado"|"cromo"|"laton"|"acero"|"negro"|"madera"|"madera_clara"),
  caras_max (presupuesto de la pieza), medidas [3 números: caja de la pieza, en cualquier orden],
  evidencia (OBLIGATORIO: en qué foto y qué se ve que justifica la pieza),
  tipo + geo:
    "torno":  geo {perfil: [[radio, altura], ...] empezando y terminando en radio 0, segmentos: 12-32,
               eje: "x"|"y"|"z"|"-x"|"-y"|"-z"}  -> roseta, cuello, botón, patas torneadas, tornillos.
    "barrido": geo {camino: [[x,y,z], ...] puntos de control, alto: [[t,mm],...], grosor: [[t,mm],...]
               (t de 0 a 1 a lo largo del camino; alto se mide sobre el eje Z), muestras: 16-30,
               puntos_seccion: 12-16, exponente: 2-4 (más alto = sección más cuadrada), punta_redonda}
               -> palancas, manijas de mueble, caños curvos.
    "caja":   geo {medidas: [x,y,z]} centrada en el origen -> placas, tablas, estantes.
    "marco":  geo {medidas: [x,y,z], hueco: [y,z]} placa con ventana rectangular (eje x = espesor).
  colocar: {pos: [x,y,z], rot: [grados x,y,z], espejo: true si es la copia del otro lado (espejo en Y)}
  despiece: [dx,dy,dz] cuánto se separa en la vista de despiece.
  copia_de: id de otra pieza para reutilizar su geometría (solo se escriben los campos que cambian).
"""

REGLAS = """
REGLAS (las controla un revisor automático):
1. Nada inventado: solo piezas que se ven en las fotos o que el usuario nombró. Cada una con evidencia.
2. Proporciones de las fotos: compará siluetas, no adivines. Si hay medidas del usuario, mandan ellas.
3. Pocas caras: respetá caras_max por pieza y presupuesto_caras total. Sin subdivisiones.
4. Simplificá mecanismos internos a formas simples pero creíbles; no modeles resortes ni roscas.
5. Las piezas tienen que tocarse (nada flotando) y las de un lado no pueden invadir la puerta.
6. Respondé SOLO con el JSON de la ficha, sin texto antes ni después.
"""


def _bloque_imagen(data: bytes) -> dict:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def hay_ia() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _llamar(contenido: list, max_tokens=16000) -> tuple[str, dict]:
    import anthropic

    cliente = anthropic.Anthropic()
    resp = cliente.messages.create(
        model=os.getenv("FABRICA_MODELO", "claude-sonnet-5-5"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": contenido}],
    )
    texto = "".join(b.text for b in resp.content if b.type == "text")
    uso = {"entrada": resp.usage.input_tokens, "salida": resp.usage.output_tokens}
    return texto, uso


def _json(texto: str) -> dict:
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ValueError("La IA no devolvió una ficha en JSON")
    return json.loads(m.group(0))


def plantilla(nombre: str) -> dict:
    return json.loads((PLANTILLAS / f"{nombre}.json").read_text(encoding="utf-8"))


def listar_plantillas() -> list[dict]:
    return [{"id": p.stem, "nombre": json.loads(p.read_text(encoding="utf-8"))["nombre"]}
            for p in sorted(PLANTILLAS.glob("*.json"))]


def disenar(fotos: list[bytes], datos: dict, aprendizajes: list[str],
            ficha_previa: dict | None = None, correcciones: list[str] | None = None) -> tuple[dict, dict]:
    """Devuelve (ficha, uso_de_tokens). Con ficha_previa + correcciones, corrige en vez de empezar."""
    ejemplo = json_compacto(plantilla("manija_jazz"))
    texto = [
        "Sos el diseñador de una fábrica de modelos 3D para SketchUp. Mirá las fotos y escribí la ficha "
        "de la pieza. No modelás polígonos: describís cada parte con perfiles, caminos y medidas.",
        FORMATO,
        REGLAS,
        "EJEMPLO DE FICHA BIEN HECHA (manija Schlage Jazz, 4.064 caras, aprobada):\n" + ejemplo,
    ]
    if aprendizajes:
        texto.append("LO QUE EL USUARIO YA PIDIÓ EN PEDIDOS ANTERIORES (respetalo):\n- " + "\n- ".join(aprendizajes))
    texto.append("DATOS DEL PEDIDO:\n" + json.dumps(datos, ensure_ascii=False, indent=1))
    if ficha_previa is not None:
        texto.append("FICHA ACTUAL:\n" + json_compacto(ficha_previa))
        texto.append("CORREGÍ ESTO Y DEVOLVÉ LA FICHA COMPLETA:\n- " + "\n- ".join(correcciones or []))
    contenido = [_bloque_imagen(f) for f in fotos[:6]]
    contenido.append({"type": "text", "text": "\n\n".join(texto)})
    respuesta, uso = _llamar(contenido)
    return _json(respuesta), uso


def revisar_visual(fotos: list[bytes], renders: list[bytes], ficha: dict) -> tuple[list[dict], dict]:
    """Compara los renders con las fotos. Devuelve diferencias [{gravedad, pieza, detalle}]."""
    contenido = [{"type": "text", "text": "FOTOS DE REFERENCIA:"}]
    contenido += [_bloque_imagen(f) for f in fotos[:4]]
    contenido.append({"type": "text", "text": "RENDERS DEL MODELO:"})
    contenido += [_bloque_imagen(r) for r in renders[:3]]
    contenido.append({"type": "text", "text": (
        "Sos el revisor de una fábrica de modelos 3D. Compará la silueta y proporciones del modelo con las "
        "fotos. Marcá solo diferencias reales y concretas (forma, proporción, pieza que falta o sobra). "
        "No marques color, textura ni iluminación. Piezas del modelo: "
        + ", ".join(p["id"] for p in ficha["piezas"])
        + '\nRespondé SOLO JSON: {"diferencias": [{"gravedad": "alta"|"media"|"baja", "pieza": "id", '
          '"detalle": "qué cambiar, con números si podés"}]}')})
    respuesta, uso = _llamar(contenido, max_tokens=3000)
    return _json(respuesta).get("diferencias", []), uso

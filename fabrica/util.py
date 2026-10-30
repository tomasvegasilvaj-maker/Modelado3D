import json
import re
from pathlib import Path


def json_compacto(obj) -> str:
    """JSON legible: listas cortas de números en una sola línea (perfiles, posiciones)."""
    texto = json.dumps(obj, indent=2, ensure_ascii=False)

    def plegar(m):
        return re.sub(r"\s+", " ", m.group(0)).replace("[ ", "[").replace(" ]", "]")

    # Listas que solo contienen números, textos cortos o listas de números.
    return re.sub(r"\[(?:\s*(?:-?[\d.]+|\"[^\"\n]{0,12}\"|\[[^\[\]{}]*\])\s*,?)+\s*\]", plegar, texto)


def guardar_json(ruta: Path, obj) -> None:
    Path(ruta).write_text(json_compacto(obj), encoding="utf-8")


def leer_json(ruta: Path, defecto=None):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto

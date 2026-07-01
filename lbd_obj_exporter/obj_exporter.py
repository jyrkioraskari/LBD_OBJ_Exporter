from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping

if TYPE_CHECKING:
    from rdflib import Graph


def _type_regex(type_names: tuple[str, ...]) -> str:
    escaped_names = "|".join(type_names)
    return f"buildingelement#({escaped_names})($|-)"


def _geometry_query(
    type_filter: str | None = None,
    type_filters: tuple[str, ...] | None = None,
    required_type_filter: str | None = None,
    excluded_type_filter: str | None = None,
    excluded_type_filters: tuple[str, ...] | None = None,
) -> str:
    filter_clauses: list[str] = []
    if type_filters is not None:
        filter_clauses.append(f'  FILTER(REGEX(STR(?type), "{_type_regex(type_filters)}"))\n')
    elif type_filter is not None:
        filter_clauses.append(f'  FILTER(REGEX(STR(?type), "buildingelement#{type_filter}($|-)"))\n')

    if required_type_filter is not None:
        filter_clauses.append(f'  ?element rdf:type bot:{required_type_filter} .\n')

    if excluded_type_filters is not None:
        for excluded_type in excluded_type_filters:
            filter_clauses.append(f'  FILTER NOT EXISTS {{ ?element rdf:type bot:{excluded_type} }}\n')
    elif excluded_type_filter is not None:
        filter_clauses.append(f'  FILTER NOT EXISTS {{ ?element rdf:type bot:{excluded_type_filter} }}\n')

    return f"""PREFIX bot: <https://w3id.org/bot#>
PREFIX fog: <https://w3id.org/fog#>
PREFIX lbd: <https://lbd.org/#>
PREFIX omg: <https://w3id.org/omg#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT ?element ?type ?isWindow ?kd ?alpha ?obj WHERE {{
  ?element omg:hasGeometry ?geometry .
  ?geometry fog:asObj_v3.0-obj ?obj .
  OPTIONAL {{ ?geometry lbd:asMTL_kd ?geometryKd }}
  OPTIONAL {{ ?element lbd:asMTL_kd ?elementKd }}
  BIND(COALESCE(?geometryKd, ?elementKd) AS ?kd)
  OPTIONAL {{ ?geometry lbd:asMTL_alpha ?geometryAlpha }}
  OPTIONAL {{ ?element lbd:asMTL_alpha ?elementAlpha }}
  OPTIONAL {{ ?element rdf:type ?type }}
  BIND(REGEX(STR(?type), "buildingelement#Window($|-)") AS ?isWindow)
  BIND(COALESCE(?geometryAlpha, ?elementAlpha) AS ?alpha)
{''.join(filter_clauses).rstrip()}
}}
"""


DEFAULT_QUERY = _geometry_query()

SAMPLE_QUERIES = {
    "Core elements": _geometry_query(type_filters=("Wall", "Beam", "Slab", "Door", "Furniture", "Roof")),
    "All": DEFAULT_QUERY,
    "No spaces": _geometry_query(excluded_type_filter="Space"),
    "Only spaces": _geometry_query(required_type_filter="Space"),
    "Windows": _geometry_query("Window"),
    "Slabs": _geometry_query("Slab"),
    "Doors": _geometry_query("Door"),
    "Walls": _geometry_query("Wall"),
    "Beams": _geometry_query("Beam"),
}


@dataclass(frozen=True)
class QueryResult:
    variables: list[str]
    rows: list[dict[str, str]]


@dataclass(frozen=True)
class ObjMesh:
    vertices: list[tuple[float, float, float]]
    faces: list[list[int]]
    face_colors: list[tuple[int, int, int]]
    face_opacities: list[float]


@dataclass(frozen=True)
class ObjFragment:
    obj_text: str
    color: tuple[int, int, int]
    opacity: float


DEFAULT_WINDOW_ALPHA = 0.1
DEFAULT_TRANSPARENT_ALPHA = 0.2


def load_turtle(path: str | Path) -> "Graph":
    from rdflib import Graph

    graph = Graph()
    graph.parse(str(path), format="turtle")
    return graph


def run_query(graph: "Graph", query: str) -> QueryResult:
    result = graph.query(query)
    variables = [str(variable) for variable in result.vars]
    rows: list[dict[str, str]] = []

    for row in result:
        bindings = {str(variable): str(value) for variable, value in row.asdict().items()}
        values: dict[str, str] = {}
        for variable in variables:
            values[variable] = bindings.get(variable, "")
        rows.append(values)

    return QueryResult(variables=variables, rows=rows)


def decode_obj_literal(value: str) -> str:
    compact_value = "".join(value.split())
    try:
        decoded = base64.b64decode(compact_value, validate=True)
    except binascii.Error as exc:
        raise ValueError("The selected row does not contain valid base64 OBJ data.") from exc

    return decoded.decode("utf-8")


def rows_to_obj_fragments(
    rows: Iterable[Mapping[str, str]],
    obj_column: str = "obj",
    color_column: str = "kd",
    alpha_column: str = "alpha",
) -> list[ObjFragment]:
    fragments_by_geometry: dict[tuple[str, str], dict[str, object]] = {}

    for row in rows:
        value = row.get(obj_column, "")
        if not value:
            continue
        key = (row.get("element", ""), value)
        opacity = _row_opacity(row, alpha_column)
        existing = fragments_by_geometry.get(key)
        if existing is not None:
            existing_is_opaque = bool(existing["is_opaque"])
            existing_has_explicit = bool(existing["has_explicit"])
            existing_is_window = bool(existing["is_window"])
            row_is_opaque = _row_is_opaque_type(row)
            row_is_window = _row_is_window(row)
            row_has_explicit = bool(row.get(alpha_column, ""))

            if row.get(color_column, ""):
                existing["color"] = parse_kd_color(row.get(color_column, ""))

            if row_is_window:
                existing["is_window"] = True
                existing["opacity"] = DEFAULT_WINDOW_ALPHA
                existing["has_explicit"] = False
                continue

            if row_is_opaque:
                if row_has_explicit:
                    existing["opacity"] = min(float(existing["opacity"]), opacity) if existing_has_explicit else opacity
                    existing["has_explicit"] = True
                elif not existing_has_explicit:
                    existing["opacity"] = 1.0
                existing["is_opaque"] = True
            elif not existing_is_opaque and not existing_has_explicit and not existing_is_window:
                existing["opacity"] = DEFAULT_TRANSPARENT_ALPHA
            continue
        fragments_by_geometry[key] = {
            "obj_text": decode_obj_literal(value),
            "color": parse_kd_color(row.get(color_column, "")),
            "opacity": opacity,
            "is_opaque": _row_is_opaque_type(row),
            "is_window": _row_is_window(row),
            "has_explicit": bool(row.get(alpha_column, "")),
        }

    if not fragments_by_geometry:
        raise ValueError(f"No query result row has an '{obj_column}' binding.")

    return [
        ObjFragment(
            obj_text=str(fragment["obj_text"]),
            color=fragment["color"],  # type: ignore[arg-type]
            opacity=float(fragment["opacity"]),
        )
        for fragment in fragments_by_geometry.values()
    ]


def _row_opacity(row: Mapping[str, str], alpha_column: str) -> float:
    if _row_is_opaque_type(row):
        alpha = row.get(alpha_column, "")
        if alpha:
            return parse_alpha(alpha)
        return 1.0
    if _row_is_window(row):
        return DEFAULT_WINDOW_ALPHA
    alpha = row.get(alpha_column, "")
    if alpha:
        return parse_alpha(alpha)
    return DEFAULT_TRANSPARENT_ALPHA


def _row_is_window(row: Mapping[str, str]) -> bool:
    if row.get("isWindow", "").lower() in {"true", "1"}:
        return True
    type_value = row.get("type", "")
    local_name = type_value.rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return local_name == "Window" or local_name.startswith("Window-")


def _row_is_opaque_type(row: Mapping[str, str]) -> bool:
    type_value = row.get("type", "")
    local_name = type_value.rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return (
        local_name == "Wall"
        or local_name.startswith("Wall-")
        or local_name == "Beam"
        or local_name.startswith("Beam-")
        or local_name == "Slab"
        or local_name.startswith("Slab-")
        or local_name == "Door"
        or local_name.startswith("Door-")
        or local_name == "Roof"
        or local_name.startswith("Roof-")
    )


def rows_to_merged_obj(rows: Iterable[Mapping[str, str]], obj_column: str = "obj") -> tuple[str, int]:
    fragments = rows_to_obj_fragments(rows, obj_column)
    return merge_obj_documents(fragment.obj_text for fragment in fragments), len(fragments)


def export_rows_to_obj(rows: Iterable[Mapping[str, str]], output_path: str | Path, obj_column: str = "obj") -> int:
    merged_obj, count = rows_to_merged_obj(rows, obj_column)
    Path(output_path).write_text(merged_obj, encoding="utf-8")
    return count


def merge_obj_documents(obj_documents: Iterable[str]) -> str:
    merged_lines: list[str] = ["# Exported from LBD OBJ Exporter"]
    vertex_offset = 0
    texture_offset = 0
    normal_offset = 0

    for index, obj_document in enumerate(obj_documents, start=1):
        local_vertices = 0
        local_textures = 0
        local_normals = 0
        merged_lines.append("")
        merged_lines.append(f"o lbd_element_{index}")

        for raw_line in obj_document.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("mtllib"):
                continue

            prefix, _, rest = line.partition(" ")
            if prefix == "v":
                local_vertices += 1
                merged_lines.append(line)
            elif prefix == "vt":
                local_textures += 1
                merged_lines.append(line)
            elif prefix == "vn":
                local_normals += 1
                merged_lines.append(line)
            elif prefix == "f":
                merged_lines.append("f " + " ".join(
                    _adjust_face_token(token, vertex_offset, texture_offset, normal_offset)
                    for token in rest.split()
                ))
            else:
                merged_lines.append(line)

        vertex_offset += local_vertices
        texture_offset += local_textures
        normal_offset += local_normals

    return "\n".join(merged_lines).rstrip() + "\n"


def parse_obj_mesh(obj_text: str) -> ObjMesh:
    vertices: list[tuple[float, float, float]] = []
    faces: list[list[int]] = []
    face_colors: list[tuple[int, int, int]] = []
    face_opacities: list[float] = []

    for raw_line in obj_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if parts[0] == "v" and len(parts) >= 4:
            vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif parts[0] == "f" and len(parts) >= 4:
            face: list[int] = []
            for token in parts[1:]:
                vertex_index = int(token.split("/")[0])
                if vertex_index < 0:
                    vertex_index = len(vertices) + vertex_index + 1
                face.append(vertex_index - 1)
            faces.append(face)
            face_colors.append(DEFAULT_MATERIAL_COLOR)
            face_opacities.append(1.0)

    return ObjMesh(vertices=vertices, faces=faces, face_colors=face_colors, face_opacities=face_opacities)


DEFAULT_MATERIAL_COLOR = (184, 190, 188)


def rows_to_obj_mesh(rows: Iterable[Mapping[str, str]]) -> tuple[ObjMesh, int]:
    fragments = rows_to_obj_fragments(rows)
    vertices: list[tuple[float, float, float]] = []
    faces: list[list[int]] = []
    face_colors: list[tuple[int, int, int]] = []
    face_opacities: list[float] = []
    vertex_offset = 0

    for fragment in fragments:
        fragment_mesh = parse_obj_mesh(fragment.obj_text)
        vertices.extend(fragment_mesh.vertices)
        for face in fragment_mesh.faces:
            shifted_face = [index + vertex_offset for index in face]
            for triangle in triangulate_face(shifted_face):
                faces.append(triangle)
                face_colors.append(fragment.color)
                face_opacities.append(fragment.opacity)
        vertex_offset += len(fragment_mesh.vertices)

    return ObjMesh(vertices=vertices, faces=faces, face_colors=face_colors, face_opacities=face_opacities), len(fragments)


def triangulate_face(face: list[int]) -> list[list[int]]:
    if len(face) < 3:
        return []
    if len(face) == 3:
        return [face]
    return [[face[0], face[index], face[index + 1]] for index in range(1, len(face) - 1)]


def parse_kd_color(value: str) -> tuple[int, int, int]:
    value = value.strip()
    if not value:
        return DEFAULT_MATERIAL_COLOR

    if value.startswith("#") and len(value) == 7:
        try:
            return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
        except ValueError:
            return DEFAULT_MATERIAL_COLOR

    parts = value.replace(",", " ").split()
    if len(parts) >= 3:
        try:
            numbers = [float(part) for part in parts[:3]]
        except ValueError:
            return DEFAULT_MATERIAL_COLOR
        if all(0.0 <= number <= 1.0 for number in numbers):
            return tuple(int(round(number * 255)) for number in numbers)
        if all(0.0 <= number <= 255.0 for number in numbers):
            return tuple(max(0, min(255, int(round(number)))) for number in numbers)

    return DEFAULT_MATERIAL_COLOR


def parse_alpha(value: str) -> float:
    value = value.strip()
    if not value:
        return 1.0
    try:
        alpha = float(value)
    except ValueError:
        return 1.0
    return max(0.0, min(1.0, alpha))


def _adjust_face_token(token: str, vertex_offset: int, texture_offset: int, normal_offset: int) -> str:
    parts = token.split("/")
    offsets = [vertex_offset, texture_offset, normal_offset]
    adjusted_parts: list[str] = []

    for index, part in enumerate(parts):
        if not part:
            adjusted_parts.append(part)
            continue

        value = int(part)
        if value > 0:
            value += offsets[index]
        adjusted_parts.append(str(value))

    return "/".join(adjusted_parts)

import base64
import tempfile
import unittest
from pathlib import Path

from lbd_obj_exporter.app import DIMMED_ALPHA, HIGHLIGHT_COLOR, LbdObjExporterWindow
from lbd_obj_exporter.obj_exporter import (
    QueryResult,
    DEFAULT_WINDOW_ALPHA,
    SAMPLE_QUERIES,
    decode_obj_literal,
    export_rows_to_obj,
    merge_obj_documents,
    parse_obj_mesh,
    parse_alpha,
    parse_kd_color,
    rows_to_merged_obj,
    rows_to_obj_mesh,
    triangulate_face,
)


class ObjExporterTest(unittest.TestCase):
    def test_decode_obj_literal(self):
        encoded = base64.b64encode(b"v 0 0 0\nf 1 1 1\n").decode("ascii")

        self.assertEqual(decode_obj_literal(encoded), "v 0 0 0\nf 1 1 1\n")

    def test_merge_rewrites_face_indices(self):
        first = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        second = "v 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3\n"

        merged = merge_obj_documents([first, second])

        self.assertIn("f 1 2 3", merged)
        self.assertIn("f 4 5 6", merged)

    def test_export_rows_to_obj(self):
        encoded = base64.b64encode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").decode("ascii")

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "selected.obj"
            count = export_rows_to_obj([{"obj": encoded}], output_path)

            self.assertEqual(count, 1)
            self.assertIn("f 1 2 3", output_path.read_text(encoding="utf-8"))

    def test_export_rows_deduplicates_same_element_geometry(self):
        encoded = base64.b64encode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").decode("ascii")
        rows = [
            {"element": "https://example.org/wall_1", "type": "bot:Element", "obj": encoded},
            {"element": "https://example.org/wall_1", "type": "beo:Wall", "obj": encoded},
        ]

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "merged.obj"
            count = export_rows_to_obj(rows, output_path)

            self.assertEqual(count, 1)
            self.assertEqual(output_path.read_text(encoding="utf-8").count("o lbd_element_"), 1)

    def test_rows_to_merged_obj_can_be_parsed_for_preview(self):
        encoded = base64.b64encode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").decode("ascii")

        merged, count = rows_to_merged_obj([{"element": "wall", "obj": encoded}])
        mesh = parse_obj_mesh(merged)

        self.assertEqual(count, 1)
        self.assertEqual(len(mesh.vertices), 3)
        self.assertEqual(mesh.faces, [[0, 1, 2]])

    def test_parse_kd_color(self):
        self.assertEqual(parse_kd_color("#804020"), (128, 64, 32))
        self.assertEqual(parse_kd_color("0.5 0.25 1.0"), (128, 64, 255))
        self.assertEqual(parse_kd_color("128 64 32"), (128, 64, 32))

    def test_parse_alpha(self):
        self.assertEqual(parse_alpha(""), 1.0)
        self.assertEqual(parse_alpha("0.42"), 0.42)
        self.assertEqual(parse_alpha("2"), 1.0)
        self.assertEqual(parse_alpha("-1"), 0.0)

    def test_rows_to_obj_mesh_uses_kd_color(self):
        encoded = base64.b64encode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").decode("ascii")

        mesh, count = rows_to_obj_mesh([{"element": "wall", "obj": encoded, "kd": "#804020", "alpha": "0.5"}])

        self.assertEqual(count, 1)
        self.assertEqual(mesh.faces, [[0, 1, 2]])
        self.assertEqual(mesh.face_colors, [(128, 64, 32)])
        self.assertEqual(mesh.face_opacities, [0.5])

    def test_rows_to_obj_mesh_uses_window_alpha_fallback_for_duplicate_geometry(self):
        encoded = base64.b64encode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").decode("ascii")
        rows = [
            {"element": "window", "type": "bot:Element", "obj": encoded, "alpha": ""},
            {"element": "window", "isWindow": "true", "type": "https://pi.pauwel.be/voc/buildingelement#Window", "obj": encoded, "alpha": ""},
        ]

        mesh, count = rows_to_obj_mesh(rows)

        self.assertEqual(count, 1)
        self.assertEqual(mesh.face_opacities, [DEFAULT_WINDOW_ALPHA])

    def test_rows_to_obj_mesh_uses_window_alpha_fallback_when_window_row_is_first(self):
        encoded = base64.b64encode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").decode("ascii")
        rows = [
            {"element": "window", "type": "beo:Window", "obj": encoded, "alpha": ""},
            {"element": "window", "type": "bot:Element", "obj": encoded, "alpha": ""},
        ]

        mesh, count = rows_to_obj_mesh(rows)

        self.assertEqual(count, 1)
        self.assertEqual(mesh.face_opacities, [DEFAULT_WINDOW_ALPHA])

    def test_rows_to_obj_mesh_keeps_wall_without_alpha_opaque(self):
        encoded = base64.b64encode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").decode("ascii")
        rows = [
            {"element": "wall", "type": "bot:Element", "obj": encoded, "alpha": ""},
            {"element": "wall", "type": "https://pi.pauwel.be/voc/buildingelement#Wall", "obj": encoded, "alpha": ""},
        ]

        mesh, count = rows_to_obj_mesh(rows)

        self.assertEqual(count, 1)
        self.assertEqual(mesh.face_opacities, [1.0])

    def test_triangulate_face(self):
        self.assertEqual(triangulate_face([0, 1, 2, 3]), [[0, 1, 2], [0, 2, 3]])

    def test_sample_queries_filter_requested_building_element_types(self):
        expected_filters = {
            "Core elements": "buildingelement#(Wall|Beam|Slab|Door|Furniture|Roof)($|-)",
            "All": None,
            "No spaces": "FILTER NOT EXISTS { ?element rdf:type bot:Space }",
            "Only spaces": "?element rdf:type bot:Space",
            "Windows": "buildingelement#Window($|-)",
            "Slabs": "buildingelement#Slab($|-)",
            "Doors": "buildingelement#Door($|-)",
            "Walls": "buildingelement#Wall($|-)",
            "Beams": "buildingelement#Beam($|-)",
        }

        self.assertEqual(set(SAMPLE_QUERIES), set(expected_filters))
        for label, expected_filter in expected_filters.items():
            query = SAMPLE_QUERIES[label]

            self.assertIn("?geometry fog:asObj_v3.0-obj ?obj", query)
            self.assertIn("?element rdf:type ?type", query)
            self.assertIn("?isWindow", query)
            if expected_filter is None:
                self.assertNotIn("FILTER(REGEX", query)
            else:
                self.assertIn(expected_filter, query)

    def test_highlight_rows_marks_selected_element_red_and_dims_others(self):
        window = object.__new__(LbdObjExporterWindow)
        window.result = QueryResult(
            variables=["element", "obj", "kd", "alpha"],
            rows=[
                {"element": "wall-1", "obj": "aaa", "kd": "#aaaaaa", "alpha": "0.5"},
                {"element": "wall-2", "obj": "bbb", "kd": "#bbbbbb", "alpha": "1.0"},
                {"element": "wall-1", "obj": "ccc", "kd": "#cccccc", "alpha": "0.7"},
            ],
        )

        rows = window._rows_with_highlighted_element(0)

        self.assertEqual(rows[0]["kd"], HIGHLIGHT_COLOR)
        self.assertEqual(rows[0]["alpha"], "1.0")
        self.assertEqual(rows[1]["kd"], "#bbbbbb")
        self.assertEqual(rows[1]["alpha"], DIMMED_ALPHA)
        self.assertEqual(rows[2]["kd"], HIGHLIGHT_COLOR)
        self.assertEqual(rows[2]["alpha"], "1.0")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .obj_exporter import (
    ObjMesh,
    QueryResult,
    SAMPLE_QUERIES,
    export_rows_to_obj,
    load_turtle,
    rows_to_obj_mesh,
    run_query,
)


def _missing_gui_dependency_message(exc: ImportError) -> str:
    return (
        "The PyVista/Qt viewer dependencies are not installed.\n\n"
        "Install them with:\n"
        "  python3 -m pip install -r requirements.txt\n\n"
        f"Import error: {exc}"
    )


try:
    os.environ.setdefault("QT_API", "pyside6")
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/lbd_obj_exporter_matplotlib")
    import numpy as np
    import pyvista as pv
    from pyvistaqt import QtInteractor
    from PySide6 import QtCore, QtWidgets
except ImportError as exc:  # pragma: no cover - exercised only without GUI deps
    _GUI_IMPORT_ERROR = exc
    np = None  # type: ignore[assignment]
    pv = None  # type: ignore[assignment]
    QtInteractor = None  # type: ignore[assignment]

    class _MissingQtCore:
        class Qt:
            TextSelectableByMouse = 0
            Vertical = 0
            Horizontal = 0

    class _MissingQtWidgets:
        QMainWindow = object

    QtCore = _MissingQtCore()  # type: ignore[assignment]
    QtWidgets = _MissingQtWidgets()  # type: ignore[assignment]
else:
    _GUI_IMPORT_ERROR = None


HIGHLIGHT_COLOR = "#dc0000"
DIMMED_ALPHA = "0.18"
DEFAULT_SAMPLE_QUERY_LABEL = "No spaces"


class LbdObjExporterWindow(QtWidgets.QMainWindow):  # type: ignore[misc, union-attr]
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LBD OBJ Exporter")
        self.resize(1280, 820)

        self.graph: Any | None = None
        self.ttl_path: Path | None = None
        self.result = QueryResult(variables=[], rows=[])
        self.preview_mesh: ObjMesh | None = None
        self.preview_count = 0
        self._highlighted_row_index: int | None = None
        self._temp_obj_path: Path | None = None
        self._resize_render_timer: Any | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        self.setCentralWidget(central)

        toolbar = QtWidgets.QHBoxLayout()
        self.open_button = QtWidgets.QPushButton("Open Turtle")
        self.run_button = QtWidgets.QPushButton("Run Query")
        self.export_button = QtWidgets.QPushButton("Export Merged OBJ")
        self.sample_query_combo = QtWidgets.QComboBox()
        self.sample_query_combo.addItems(SAMPLE_QUERIES.keys())
        self.sample_query_combo.setMinimumWidth(150)
        self.sample_query_combo.setCurrentText(DEFAULT_SAMPLE_QUERY_LABEL)
        self.file_label = QtWidgets.QLabel("No file loaded")
        self.file_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.run_button)
        toolbar.addWidget(self.sample_query_combo)
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.file_label, 1)
        layout.addLayout(toolbar)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.setOpaqueResize(False)
        layout.addWidget(splitter, 1)

        self.query_text = QtWidgets.QPlainTextEdit()
        self.query_text.setPlainText(SAMPLE_QUERIES[DEFAULT_SAMPLE_QUERY_LABEL])
        self.query_text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        splitter.addWidget(self.query_text)

        lower_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        lower_splitter.setOpaqueResize(False)
        splitter.addWidget(lower_splitter)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([360, 440])

        self.result_table = QtWidgets.QTableWidget()
        self.result_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.result_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setStyleSheet(
            "QTableWidget::item:selected { "
            "background-color: #e8f2ff; "
            "color: #111111; "
            "}"
        )
        lower_splitter.addWidget(self.result_table)

        viewer_frame = QtWidgets.QFrame()
        viewer_layout = QtWidgets.QVBoxLayout(viewer_frame)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        self.plotter = QtInteractor(viewer_frame)
        viewer_layout.addWidget(self.plotter.interactor)
        lower_splitter.addWidget(viewer_frame)
        lower_splitter.setStretchFactor(0, 2)
        lower_splitter.setStretchFactor(1, 3)

        self.status = QtWidgets.QLabel("Select an LBD Turtle file to begin.")
        layout.addWidget(self.status)

        self.open_button.clicked.connect(self.open_turtle)
        self.run_button.clicked.connect(self.run_current_query)
        self.export_button.clicked.connect(self.export_merged)
        self.sample_query_combo.currentTextChanged.connect(self.apply_sample_query)
        self.result_table.cellClicked.connect(self._toggle_highlighted_row)
        splitter.splitterMoved.connect(self._request_resize_render)
        lower_splitter.splitterMoved.connect(self._request_resize_render)

        self._configure_plotter()

    def _configure_plotter(self) -> None:
        self.plotter.set_background("#f2f4f5")
        self.plotter.enable_anti_aliasing()
        try:
            self.plotter.enable_depth_peeling(number_of_peels=8, occlusion_ratio=0.0)
        except Exception:
            pass
        self.plotter.show_axes()
        self.plotter.add_text("No OBJ preview", position="upper_left", color="#555555", font_size=10)

    def open_turtle(self) -> None:
        selected, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open LBD Turtle file",
            "",
            "Turtle files (*.ttl *.turtle);;All files (*.*)",
        )
        if not selected:
            return

        path = Path(selected)
        try:
            self.graph = load_turtle(path)
        except Exception as exc:
            self._show_error("Could not load Turtle file", exc)
            return

        self.ttl_path = path
        self.file_label.setText(str(path))
        self.status.setText(f"Loaded {len(self.graph)} triples from {path.name}.")
        self.sample_query_combo.setCurrentText(DEFAULT_SAMPLE_QUERY_LABEL)
        self.apply_sample_query(self.sample_query_combo.currentText())

    def apply_sample_query(self, label: str) -> None:
        query = SAMPLE_QUERIES.get(label)
        if query is None:
            return

        self.query_text.setPlainText(query)
        self.status.setText(f"Loaded sample query: {label}.")
        if self.graph is not None:
            self.run_current_query()

    def run_current_query(self) -> None:
        if self.graph is None:
            self._show_info("No Turtle file", "Open an LBD Turtle file first.")
            return

        query = self.query_text.toPlainText().strip()
        if not query:
            self._show_info("No query", "Enter a SPARQL SELECT query.")
            return

        try:
            self.result = run_query(self.graph, query)
        except Exception as exc:
            self._show_error("SPARQL query failed", exc)
            return

        self._highlighted_row_index = None
        self._show_results()
        self.refresh_preview(show_errors=False)
        self.status.setText(f"Query returned {len(self.result.rows)} rows.")

    def refresh_preview(self, show_errors: bool = True) -> None:
        if not self.result.rows:
            self.preview_mesh = None
            self.preview_count = 0
            self._clear_plotter("No OBJ preview")
            if show_errors:
                self._show_info("No query results", "Run a query that returns OBJ geometry first.")
            return

        try:
            self.preview_mesh, self.preview_count = rows_to_obj_mesh(self.result.rows)
            if not self.preview_mesh.vertices or not self.preview_mesh.faces:
                raise ValueError("The query returned OBJ data, but no mesh faces could be parsed.")
        except Exception as exc:
            self.preview_mesh = None
            self.preview_count = 0
            self._clear_plotter("No OBJ preview")
            if show_errors:
                self._show_error("OBJ preview failed", exc)
            return

        self._show_mesh(self.preview_mesh)
        self.status.setText(
            f"Previewing {self.preview_count} unique geometry object(s), "
            f"{len(self.preview_mesh.vertices)} vertices, {len(self.preview_mesh.faces)} faces."
        )

    def reset_camera(self) -> None:
        self.plotter.reset_camera()
        self.plotter.render()

    def export_merged(self) -> None:
        if not self.result.rows:
            self._show_info("No query results", "Run a query that returns OBJ geometry first.")
            return

        output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Merged OBJ",
            "",
            "Wavefront OBJ (*.obj);;All files (*.*)",
        )
        if not output_path:
            return

        try:
            count = export_rows_to_obj(self.result.rows, output_path)
        except Exception as exc:
            self._show_error("OBJ export failed", exc)
            return

        self.status.setText(f"Exported {count} unique geometry object(s) to {output_path}.")
        self._show_info("OBJ export complete", f"Exported {count} geometry object(s).")

    def _show_results(self) -> None:
        visible_columns = [column for column in self.result.variables if column != "obj"]
        if not visible_columns:
            visible_columns = self.result.variables

        self.result_table.blockSignals(True)
        self.result_table.clear()
        self.result_table.setColumnCount(len(visible_columns))
        self.result_table.setRowCount(len(self.result.rows))
        self.result_table.setHorizontalHeaderLabels(visible_columns)

        for row_index, row in enumerate(self.result.rows):
            for column_index, column in enumerate(visible_columns):
                item = QtWidgets.QTableWidgetItem(self._shorten(row.get(column, "")))
                self.result_table.setItem(row_index, column_index, item)

        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for column_index, column in enumerate(visible_columns):
            self.result_table.setColumnWidth(column_index, 420 if column == "element" else 180)
        self.result_table.blockSignals(False)

    def _to_pyvista_polydata(self, mesh: ObjMesh) -> Any:
        points = np.array(mesh.vertices, dtype=float)
        face_parts: list[int] = []
        face_colors: list[tuple[int, int, int]] = []
        face_colors_alpha: list[tuple[int, int, int, int]] = []

        for face, color, opacity in zip(mesh.faces, mesh.face_colors, mesh.face_opacities):
            if len(face) < 3:
                continue
            face_parts.extend([len(face), *face])
            alpha = int(round(opacity * 255))
            face_colors.append(color)
            face_colors_alpha.append((*color, alpha))

        faces = np.array(face_parts, dtype=np.int64)
        polydata = pv.PolyData(points, faces)
        polydata.cell_data["rgb"] = np.array(face_colors, dtype=np.uint8)
        polydata.cell_data["rgba"] = np.array(face_colors_alpha, dtype=np.uint8)
        return polydata

    def _show_polydata(self, polydata: Any, reset_camera: bool = True) -> None:
        self.plotter.clear()
        self._add_polydata(polydata)
        self.plotter.add_axes()
        if reset_camera:
            self.plotter.reset_camera()
        self.plotter.render()

    def _show_mesh(self, mesh: ObjMesh, reset_camera: bool = True) -> None:
        self.plotter.clear()

        opaque_faces = [
            index for index, opacity in enumerate(mesh.face_opacities)
            if opacity >= 0.999
        ]
        transparent_faces_by_opacity: dict[float, list[int]] = {}
        for index, opacity in enumerate(mesh.face_opacities):
            if opacity >= 0.999:
                continue
            transparent_faces_by_opacity.setdefault(round(opacity, 3), []).append(index)

        if opaque_faces:
            opaque_mesh = self._mesh_from_face_indices(mesh, opaque_faces)
            self._add_polydata(self._to_pyvista_polydata(opaque_mesh), use_cell_alpha=False)

        for opacity, face_indices in transparent_faces_by_opacity.items():
            transparent_mesh = self._mesh_from_face_indices(mesh, face_indices)
            self._add_polydata(
                self._to_pyvista_polydata(transparent_mesh),
                opacity=opacity,
                use_cell_alpha=False,
            )

        self.plotter.add_axes()
        if reset_camera:
            self.plotter.reset_camera()
        self.plotter.render()

    def _mesh_from_face_indices(self, mesh: ObjMesh, face_indices: list[int]) -> ObjMesh:
        return ObjMesh(
            vertices=mesh.vertices,
            faces=[mesh.faces[index] for index in face_indices],
            face_colors=[mesh.face_colors[index] for index in face_indices],
            face_opacities=[mesh.face_opacities[index] for index in face_indices],
        )

    def _show_highlighted_polydata(self, selected_polydata: Any, dimmed_polydata: Any | None) -> None:
        self.plotter.clear()
        if dimmed_polydata is not None:
            self._add_polydata(dimmed_polydata, opacity=float(DIMMED_ALPHA), use_cell_alpha=False)
        self._add_polydata(selected_polydata, opacity=1.0, use_cell_alpha=False)
        self.plotter.add_axes()
        self.plotter.render()

    def _add_polydata(self, polydata: Any, opacity: float = 1.0, use_cell_alpha: bool = True) -> None:
        has_transparency = bool(np.any(polydata.cell_data["rgba"][:, 3] < 255))
        mesh_options = {
            "mesh": polydata,
            "scalars": "rgba" if has_transparency and use_cell_alpha else "rgb",
            "rgb": True,
            "opacity": opacity,
            "show_edges": False,
            "silhouette": {"color": "#000000", "line_width": 2},
            "culling": False,
            "lighting": True,
            "smooth_shading": False,
            "preference": "cell",
            "use_transparency": has_transparency and use_cell_alpha or opacity < 1.0,
            "force_opaque": not has_transparency and opacity >= 1.0,
            "render": False,
        }
        try:
            actor = self.plotter.add_mesh(**mesh_options)
        except TypeError:
            mesh_options.pop("silhouette")
            actor = self.plotter.add_mesh(**mesh_options)

        prop = actor.GetProperty()
        prop.BackfaceCullingOff()
        prop.FrontfaceCullingOff()
        prop.LightingOn()
        prop.SetRepresentationToSurface()
        prop.SetInterpolationToFlat()
        prop.SetOpacity(opacity)
        prop.SetAmbient(0.35)
        prop.SetDiffuse(0.75)
        prop.SetSpecular(0.12)
        prop.SetSpecularPower(18)
        self._add_object_outlines(polydata)

    def _add_object_outlines(self, polydata: Any) -> None:
        try:
            outlines = polydata.extract_feature_edges(
                boundary_edges=True,
                feature_edges=True,
                manifold_edges=False,
                non_manifold_edges=True,
                feature_angle=25,
            )
        except Exception:
            return

        if getattr(outlines, "n_cells", 0) == 0:
            return

        self.plotter.add_mesh(
            outlines,
            color="#000000",
            line_width=4,
            render=False,
        )

    def _toggle_highlighted_row(self, row_index: int, _column_index: int) -> None:
        if self._highlighted_row_index == row_index:
            self._clear_highlight()
            return

        self._highlight_row(row_index)

    def _highlight_row(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self.result.rows):
            self.refresh_preview(show_errors=False)
            return

        try:
            selected_rows, dimmed_rows = self._split_highlighted_rows(row_index)
            selected_mesh, _selected_count = rows_to_obj_mesh(selected_rows)
            if not selected_mesh.vertices or not selected_mesh.faces:
                self.refresh_preview(show_errors=False)
                return
            selected_polydata = self._to_pyvista_polydata(selected_mesh)
            dimmed_polydata = None
            if dimmed_rows:
                dimmed_mesh, _dimmed_count = rows_to_obj_mesh(dimmed_rows)
                if dimmed_mesh.vertices and dimmed_mesh.faces:
                    dimmed_polydata = self._to_pyvista_polydata(dimmed_mesh)
        except Exception as exc:
            self.status.setText(f"Could not highlight selected row: {exc}")
            self.refresh_preview(show_errors=False)
            return

        self.preview_mesh = selected_mesh
        self._highlighted_row_index = row_index
        self._show_highlighted_polydata(selected_polydata, dimmed_polydata)
        self.status.setText(f"Highlighted row {row_index + 1}.")

    def _clear_highlight(self) -> None:
        self._highlighted_row_index = None
        self.result_table.blockSignals(True)
        self.result_table.clearSelection()
        self.result_table.blockSignals(False)

        try:
            mesh, _count = rows_to_obj_mesh(self.result.rows)
        except Exception as exc:
            self.status.setText(f"Could not clear highlight: {exc}")
            self.refresh_preview(show_errors=False)
            return

        self.preview_mesh = mesh
        self._show_mesh(mesh, reset_camera=False)
        self.status.setText("Highlight cleared.")

    def _rows_with_highlighted_element(self, row_index: int) -> list[dict[str, str]]:
        selected = self.result.rows[row_index]
        selected_element = selected.get("element", "")
        selected_obj = selected.get("obj", "")
        highlighted_rows: list[dict[str, str]] = []

        for row in self.result.rows:
            next_row = dict(row)
            if selected_element:
                is_selected = row.get("element", "") == selected_element
            else:
                is_selected = bool(selected_obj) and row.get("obj", "") == selected_obj
            if is_selected:
                next_row["kd"] = HIGHLIGHT_COLOR
                next_row["alpha"] = "1.0"
            else:
                next_row["alpha"] = DIMMED_ALPHA
            highlighted_rows.append(next_row)

        return highlighted_rows

    def _split_highlighted_rows(self, row_index: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        highlighted_rows = self._rows_with_highlighted_element(row_index)
        selected_rows: list[dict[str, str]] = []
        dimmed_rows: list[dict[str, str]] = []

        for row in highlighted_rows:
            if row.get("kd") == HIGHLIGHT_COLOR and row.get("alpha") == "1.0":
                selected_rows.append(row)
            else:
                dimmed_rows.append(row)

        return selected_rows, dimmed_rows

    def _clear_plotter(self, text: str) -> None:
        self.plotter.clear()
        self.plotter.add_text(text, position="upper_left", color="#555555", font_size=10)
        self.plotter.render()

    def _request_resize_render(self) -> None:
        if self._resize_render_timer is not None:
            self._resize_render_timer.stop()
        else:
            self._resize_render_timer = QtCore.QTimer(self)
            self._resize_render_timer.setSingleShot(True)
            self._resize_render_timer.timeout.connect(self._render_after_resize)
        self._resize_render_timer.start(150)

    def _render_after_resize(self) -> None:
        self.plotter.render()

    def _show_error(self, title: str, exc: Exception) -> None:
        QtWidgets.QMessageBox.critical(self, title, str(exc))

    def _show_info(self, title: str, message: str) -> None:
        QtWidgets.QMessageBox.information(self, title, message)

    @staticmethod
    def _shorten(value: str, limit: int = 180) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 1] + "..."

    def closeEvent(self, event: Any) -> None:
        self.plotter.close()
        if self._temp_obj_path is not None:
            try:
                self._temp_obj_path.unlink(missing_ok=True)
            except OSError:
                pass
        super().closeEvent(event)


def main() -> None:
    if _GUI_IMPORT_ERROR is not None:
        print(_missing_gui_dependency_message(_GUI_IMPORT_ERROR), file=sys.stderr)
        raise SystemExit(1)

    app = QtWidgets.QApplication(sys.argv)
    window = LbdObjExporterWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()

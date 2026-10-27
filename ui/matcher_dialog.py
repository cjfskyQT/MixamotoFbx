"""
Manual bone matcher dialog.

A two-column table:

    [ Mixamo bone ]  [ Target bone (editable) ]  [ status ]  [ Drive ]

Buttons:

    * "Check"  - validates every filled target against the loaded
                 target skeleton.  Rows whose target doesn't exist
                 are flagged with a red X; rows whose target matches
                 get a green OK.
    * "Clear All" - empties every target entry (keeps Drive state).
    * "Generate" - closes the dialog with ``Dialog.Accepted`` ONLY
                   when at least one row is active AND every active
                   target resolves to a real bone.  Otherwise the
                   user is told what to fix first.
    * "Cancel" - close dialog rejecting changes.

Behavior:

    * Typing a target name auto-ticks "Drive" for that row, so a
      newly filled cell is driven by default.
    * Unticking Drive (or emptying the cell) skips the bone.
    * "Generate" refuses to accept a mapping with zero active rows.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from ..qt_compat import QtCore
Qt = QtCore.Qt
from ..qt_compat import QtGui
QColor, QBrush = QtGui.QColor, QtGui.QBrush
from ..qt_compat import QtWidgets
QCheckBox = QtWidgets.QCheckBox
QDialog = QtWidgets.QDialog
QDialogButtonBox = QtWidgets.QDialogButtonBox
QHBoxLayout = QtWidgets.QHBoxLayout
QHeaderView = QtWidgets.QHeaderView
QLabel = QtWidgets.QLabel
QLineEdit = QtWidgets.QLineEdit
QMessageBox = QtWidgets.QMessageBox
QPushButton = QtWidgets.QPushButton
QTableWidget = QtWidgets.QTableWidget
QTableWidgetItem = QtWidgets.QTableWidgetItem
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget

from ..bone_matcher import (
    BoneMap,
    MixamoTarget,
    duplicate_targets,
    leaf_name,
    sorted_bones,
    verify_map,
    resolve_name,
)


COL_MIXAMO = 0
COL_TARGET = 1
COL_OK = 2
COL_ENABLED = 3


class _Row:
    """Lightweight per-row widget container."""
    __slots__ = ("mixamo", "edit", "ok_item", "enabled")

    def __init__(self, mixamo: str, edit: QLineEdit,
                 ok_item: QTableWidgetItem, enabled: QCheckBox):
        self.mixamo = mixamo
        self.edit = edit
        self.ok_item = ok_item
        self.enabled = enabled


class MatcherDialog(QDialog):
    """Manual bone mapping dialog."""

    def __init__(self,
                 bone_map: BoneMap,
                 target_bones: Iterable[str],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Bone Matcher - Mixamo -> Target")
        self.resize(760, 600)

        # Use leaf names internally so namespaces don't break the
        # "exists in target" check, but also keep the original full
        # name so writers can find the actual node.
        import copy
        self._bone_map = copy.deepcopy(bone_map)
        target_bones = list(target_bones)
        self._target_set = {leaf_name(t) for t in target_bones}
        self._target_full_index = target_bones
        self._rows: List[_Row] = []

        info = QLabel(
            "Left column lists the mixamo bones found in the source FBX.\n"
            "Right column is editable: type the matching bone name from "
            "your target skeleton (namespace is auto-stripped).\n"
            "Leave a row empty (or untick Drive) to skip that bone - it "
            "will not be animated.  Type-check is automatic: a non-empty "
            "value ticks Drive and the row is verified when you press "
            "Check or Generate."
        )
        info.setWordWrap(True)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            ["Mixamo bone", "Target bone", "Status", "Drive"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_MIXAMO, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_TARGET, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_OK, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_ENABLED, QHeaderView.ResizeMode.ResizeToContents)

        self._populate()

        check_btn = QPushButton("Check")
        check_btn.clicked.connect(self._check)

        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self._clear_all)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Generate")
        button_box.accepted.connect(self._on_generate)
        button_box.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(check_btn)
        top.addWidget(clear_btn)
        top.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(button_box)

    # ------------------------------------------------------------------
    # Population / editing
    # ------------------------------------------------------------------

    def _populate(self) -> None:
        rows = sorted_bones(self._bone_map)
        self.table.setRowCount(len(rows))
        for r, row_data in enumerate(rows):
            mixamo_item = QTableWidgetItem(row_data.mixamo)
            mixamo_item.setFlags(mixamo_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, COL_MIXAMO, mixamo_item)

            edit = QLineEdit(row_data.target)
            # Typing into the cell auto-ticks Drive for that row.
            edit.textChanged.connect(
                lambda text, _r=r: self._on_target_typed(self._rows[_r], text))
            self.table.setCellWidget(r, COL_TARGET, edit)

            ok_item = QTableWidgetItem("")
            ok_item.setFlags(ok_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            ok_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, COL_OK, ok_item)

            enabled_cb = QCheckBox()
            enabled_cb.setChecked(row_data.enabled)
            wrap = QWidget()
            lay = QHBoxLayout(wrap)
            lay.addWidget(enabled_cb)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(r, COL_ENABLED, wrap)

            self._rows.append(_Row(row_data.mixamo, edit, ok_item, enabled_cb))

    def _on_target_typed(self, row: _Row, text: str) -> None:
        """Auto-tick Drive when the user types a non-empty target."""
        if text.strip() and not row.enabled.isChecked():
            row.enabled.setChecked(True)
        elif not text.strip() and row.enabled.isChecked():
            # Empty cell -> untick (skip is the safe default).
            row.enabled.setChecked(False)

    def _clear_all(self) -> None:
        for r in self._rows:
            r.edit.clear()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _sync_to_map(self) -> None:
        for r in self._rows:
            tgt = r.edit.text().strip()
            self._bone_map.set_target(r.mixamo, tgt)
            self._bone_map.entries[r.mixamo].enabled = (
                r.enabled.isChecked() and bool(tgt)
            )

    def _check(self) -> None:
        """Refresh on-disk map from widgets, then validate every enabled row."""
        self._sync_to_map()
        issues = verify_map(self._bone_map, self._target_full_index)

        bad = set()
        for row in self._bone_map.entries.values():
            if row.is_active():
                try:
                    resolve_name(row.target, self._target_full_index)
                except ValueError:
                    bad.add(row.mixamo)

        for r in self._rows:
            r.ok_item.setText("")
            r.ok_item.setBackground(QBrush(QColor(0, 0, 0, 0)))
            if r.enabled.isChecked() and r.edit.text().strip():
                if r.mixamo in bad:
                    r.ok_item.setText("X")
                    r.ok_item.setBackground(QBrush(QColor(255, 80, 80)))
                else:
                    r.ok_item.setText("OK")
                    r.ok_item.setBackground(QBrush(QColor(80, 200, 120)))

        if issues:
            QMessageBox.warning(
                self, "Bone check",
                "Some target bones do not exist in the rig:\n\n" +
                "\n".join(issues))
        else:
            QMessageBox.information(
                self, "Bone check",
                "All enabled target bones exist.")

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def _on_generate(self) -> None:
        self._sync_to_map()

        # Hard requirement 1: at least one row must be active.
        if not any(e.is_active() for e in self._bone_map.entries.values()):
            QMessageBox.warning(
                self, "Nothing to drive",
                "No bones are selected.  Fill in at least one target "
                "bone name and tick Drive (or leave the target cell "
                "non-empty to auto-tick Drive).")
            return

        # Hard requirement 2: every active target must exist.
        issues = verify_map(self._bone_map, self._target_full_index)
        if issues:
            QMessageBox.warning(
                self, "Target bones missing",
                "The following target bones do not exist in the rig:\n\n"
                + "\n".join(issues) +
                "\n\nFix the names or untick Drive on those rows.")
            return

        dups = duplicate_targets(self._bone_map)
        if dups:
            QMessageBox.warning(self, "Duplicate mappings", "Each target may be mapped once: " + ', '.join(dups))
            return

        self.accept()

    def result_map(self) -> BoneMap:
        return self._bone_map

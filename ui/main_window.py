"""
Top-level Qt window for CapoeiraRetarget.

Three modes (radio buttons):

* **A. ADV / HIK FBX** - target FBX in standard Maya HIK naming,
  ADV bone auto-match, no dialog.
* **B. Custom FBX** - opens the manual matcher dialog so you can
  pair mixamo bones to the rig's custom names.  Leave blank to
  skip a bone.  Mapping is cached and reused for every file
  in the batch.
* **C. Maya ADV scene** - drives controllers in the open Maya
  scene (or loads the supplied .ma via mayapy). Bakes FK first,
  then matches and verifies editable leg IK controls when enabled.

For batch processing, pick a source *directory* and the tool will
process every *.fbx in it.  For a single file, pick a file instead
(modes A and B will pop a Save-As dialog at the end; mode C drives
the scene).
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

from ..qt_compat import QtWidgets
QApplication = QtWidgets.QApplication
QCheckBox = QtWidgets.QCheckBox
QFileDialog = QtWidgets.QFileDialog
QFormLayout = QtWidgets.QFormLayout
QGroupBox = QtWidgets.QGroupBox
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QLineEdit = QtWidgets.QLineEdit
QMainWindow = QtWidgets.QMainWindow
QMessageBox = QtWidgets.QMessageBox
QProgressBar = QtWidgets.QProgressBar
QPushButton = QtWidgets.QPushButton
QRadioButton = QtWidgets.QRadioButton
QSpinBox = QtWidgets.QSpinBox
QStatusBar = QtWidgets.QStatusBar
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget

from ..fbx_reader import FbxUnavailable, load
from ..bone_matcher import (
    BoneMap,
    auto_match_adv,
    auto_match_by_name,
    auto_match_controllers,
    skeleton_signature,
)
from ..retarget_tool import (
    RetargetResult,
    retarget_fbx_adv,
    retarget_fbx_custom,
    retarget_maya_scene,
    save_bone_map,
)


class RetargetWindow(QMainWindow):
    _AUTO_FRAME = -1_000_000

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CapoeiraRetarget - mixamo -> ADV / mGear / Maya")
        self.resize(720, 620)

        # remembered manual mapping for batch mode B / C
        self._manual_map: Optional[BoneMap] = None
        self._target_bones_cache: Dict[str, List[str]] = {}

        self._build_ui()
        self._refresh_mode_state()
        for edit in (self.src_edit, self.tgt_edit, self.ma_edit):
            edit.textChanged.connect(self._invalidate_mapping)
        for mode in (self.mode_adv, self.mode_custom, self.mode_maya):
            mode.toggled.connect(self._invalidate_mapping)
        self.cp_chk.toggled.connect(self._refresh_mode_state)
        self.legs_ik_chk.toggled.connect(self._refresh_mode_state)

    def _invalidate_mapping(self, *args):
        self._manual_map = None
        self._target_bones_cache.clear()

    def _get_fbx_target_bones(self, target_path: str) -> List[str]:
        key = os.path.normcase(os.path.abspath(target_path))
        if key not in self._target_bones_cache:
            self._target_bones_cache[key] = list(load(target_path).skeleton.bones.keys())
        return self._target_bones_cache[key]

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ---- Source ----
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText(
            "Pick a single mixamo FBX OR a directory full of FBX…")
        self.src_file_btn = QPushButton("File…")
        self.src_file_btn.clicked.connect(self._pick_source_file)
        self.src_dir_btn = QPushButton("Folder…")
        self.src_dir_btn.clicked.connect(self._pick_source_dir)

        # ---- Target FBX (cases A & B) ----
        self.tgt_edit = QLineEdit()
        self.tgt_edit.setPlaceholderText("Pick the target rig FBX…")
        self.tgt_btn = QPushButton("Browse…")
        self.tgt_btn.clicked.connect(self._pick_target)

        # ---- Maya .ma (case C) ----
        self.ma_edit = QLineEdit()
        self.ma_edit.setPlaceholderText(
            "Optional: pick the ADV/mGear .ma file.  "
            "Its FK controllers will be auto-matched to mixamo bones.")
        self.ma_btn = QPushButton("Browse…")
        self.ma_btn.clicked.connect(self._pick_ma)

        # ---- Output dir ----
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText(
            "Optional: where to write baked FBX / .ma files.  "
            "Defaults next to the source.")
        self.out_btn = QPushButton("Browse…")
        self.out_btn.clicked.connect(self._pick_out_dir)

        # ---- Mode ----
        self.mode_adv = QRadioButton(
            "A. ADV / HIK FBX  -  ADV bone auto-match, no dialog")
        self.mode_custom = QRadioButton(
            "B. Custom-named FBX  -  opens manual bone matcher")
        self.mode_maya = QRadioButton(
            "C. Maya ADV scene  -  editable controllers")
        self.mode_adv.setChecked(True)
        for rb in (self.mode_adv, self.mode_custom, self.mode_maya):
            rb.toggled.connect(self._refresh_mode_state)

        # ---- Match bones (only enabled when needed) ----
        self.match_btn = QPushButton("Match bones…")
        self.match_btn.clicked.connect(self._open_matcher)

        # ---- Frame range ----
        self.start_sb = QSpinBox()
        self.start_sb.setRange(self._AUTO_FRAME, 1_000_000)
        self.start_sb.setSpecialValueText("auto")
        self.start_sb.setValue(self._AUTO_FRAME)
        self.end_sb = QSpinBox()
        self.end_sb.setRange(self._AUTO_FRAME, 1_000_000)
        self.end_sb.setSpecialValueText("auto")
        self.end_sb.setValue(self._AUTO_FRAME)

        # ---- Options ----
        self.fk_chk = QCheckBox("Force FK on ADV IK/FK controllers")
        self.fk_chk.setChecked(True)
        self.legs_ik_chk = QCheckBox("双腿转 IK（保留原动作，方便修改脚部）")
        self.legs_ik_chk.setChecked(True)
        self.legs_ik_chk.setToolTip("先生成 FK 动画，再匹配脚部、膝盖方向和脚趾，烘焙 IK 并验证轨迹；手臂保持 FK。")
        self.convert_ik_btn = QPushButton("已有 FK 动画转双腿 IK…")
        self.convert_ik_btn.clicked.connect(self._convert_existing_legs_ik)
        self.contact_chk = QCheckBox("脚部轨迹修正（保留原动作与脚趾自然姿态）")
        self.contact_chk.setChecked(True)
        self.contact_chk.setToolTip("整段连续跟随源脚位移，保留脚掌和脚趾的绑定角度并叠加源动画旋转变化，不在接地边界切换轨迹。")
        self.cp_chk = QCheckBox("Use Maya commandPort (TCP)")
        self.cp_chk.setChecked(False)
        self.host_edit = QLineEdit("127.0.0.1")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(7001)

        # ---- Cache toggle ----
        self.cache_chk = QCheckBox(
            "Save matcher map to disk (reused for every file in batch)")
        self.cache_chk.setChecked(True)

        # ---- Generate / progress ----
        self.gen_btn = QPushButton("Generate")
        self.gen_btn.setFixedHeight(44)
        self.gen_btn.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.gen_btn.clicked.connect(self._on_generate)
        self.progress = QProgressBar()
        self.progress.setVisible(False)

        # ---- Layouts ----
        def hrow(edit: QLineEdit, btn: QPushButton) -> QHBoxLayout:
            h = QHBoxLayout()
            h.addWidget(edit, 1)
            h.addWidget(btn)
            return h

        src_row = QHBoxLayout()
        src_row.addWidget(self.src_edit, 1)
        src_row.addWidget(self.src_file_btn)
        src_row.addWidget(self.src_dir_btn)

        tgt_box = QGroupBox("Target rig FBX  (modes A & B)")
        tgt_layout = QVBoxLayout(tgt_box)
        tgt_layout.addLayout(hrow(self.tgt_edit, self.tgt_btn))

        ma_box = QGroupBox("Maya scene  (mode C)")
        ma_layout = QVBoxLayout(ma_box)
        ma_layout.addLayout(hrow(self.ma_edit, self.ma_btn))
        ma_layout.addWidget(self.cp_chk)
        cp_row = QHBoxLayout()
        cp_row.addWidget(QLabel("Host:"))
        cp_row.addWidget(self.host_edit)
        cp_row.addWidget(QLabel("Port:"))
        cp_row.addWidget(self.port_edit)
        cp_row.addStretch(1)
        ma_layout.addLayout(cp_row)

        out_box = QGroupBox("Output (optional)")
        out_layout = QVBoxLayout(out_box)
        out_layout.addLayout(hrow(self.out_edit, self.out_btn))

        mode_box = QGroupBox("Mode")
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.addWidget(self.mode_adv)
        mode_layout.addWidget(self.mode_custom)
        mode_layout.addWidget(self.mode_maya)
        mode_layout.addWidget(self.match_btn)
        mode_layout.addWidget(self.cache_chk)
        mode_layout.addWidget(self.contact_chk)

        range_box = QGroupBox("Frame range  (auto = use source range)")
        range_layout = QFormLayout(range_box)
        range_layout.addRow("Start:", self.start_sb)
        range_layout.addRow("End:",   self.end_sb)

        opt_box = QGroupBox("Options")
        opt_layout = QVBoxLayout(opt_box)
        opt_layout.addWidget(self.fk_chk)
        opt_layout.addWidget(self.legs_ik_chk)
        opt_layout.addWidget(self.convert_ik_btn)

        central = QWidget()
        v = QVBoxLayout(central)
        # Source on top so it's visible immediately
        src_box = QGroupBox("Source mixamo FBX  (file or folder)")
        src_box_layout = QVBoxLayout(src_box)
        src_box_layout.addLayout(src_row)
        v.addWidget(src_box)
        v.addWidget(mode_box)
        v.addWidget(tgt_box)
        v.addWidget(ma_box)
        v.addWidget(out_box)
        v.addWidget(range_box)
        v.addWidget(opt_box)
        v.addWidget(self.gen_btn)
        v.addWidget(self.progress)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

    # ------------------------------------------------------------------
    # Mode-dependent enable / disable
    # ------------------------------------------------------------------

    def _refresh_mode_state(self) -> None:
        custom = self.mode_custom.isChecked()
        maya = self.mode_maya.isChecked()
        # Match button is useful for B and C (auto-fill is best-effort).
        self.match_btn.setEnabled(custom or maya)
        # Target FBX only for A & B
        self.tgt_edit.setEnabled(not maya)
        self.tgt_btn.setEnabled(not maya)
        # Maya .ma only for C
        self.ma_edit.setEnabled(maya)
        self.ma_btn.setEnabled(maya)
        # commandPort only for C
        self.legs_ik_chk.setEnabled(maya)
        self.fk_chk.setEnabled(maya and not self.legs_ik_chk.isChecked())
        self.cp_chk.setEnabled(maya)
        self.host_edit.setEnabled(maya and self.cp_chk.isChecked())
        self.port_edit.setEnabled(maya and self.cp_chk.isChecked())

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------

    def _convert_existing_legs_ik(self):
        source, _ = QFileDialog.getOpenFileName(self, "选择已有 FK 动画场景", "", "Maya ASCII (*.ma)")
        if not source: return
        default = os.path.splitext(source)[0] + "_legs_ik.ma"
        output, _ = QFileDialog.getSaveFileName(self, "另存 IK 动画场景", default, "Maya ASCII (*.ma)")
        if not output: return
        if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(output)):
            QMessageBox.warning(self, "无法转换", "请选择不同的输出文件，保留原 FK 场景。")
            return
        from ..maya_runtime import run_job
        self.convert_ik_btn.setEnabled(False)
        self.statusBar().showMessage("正在烘焙双腿 IK 并验证轨迹…")
        QApplication.processEvents()
        try:
            result = run_job(dict(operation='legs_ik', target=source, output=output))
            self.statusBar().showMessage("IK 转换完成：" + result['output_path'])
            QMessageBox.information(self, "转换完成", "已保存：" + result['output_path'] + "\n最大关节位置误差：%.5f cm" % result['max_position_error_cm'])
        except Exception as exc:
            QMessageBox.critical(self, "IK 转换失败", str(exc))
            self.statusBar().showMessage("IK 转换失败；原场景保留。")
        finally:
            self.convert_ik_btn.setEnabled(True)

    def _pick_source_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick a mixamo source FBX", "", "FBX (*.fbx)")
        if path:
            self.src_edit.setText(path)
            self.statusBar().showMessage(f"Source: {path}")

    def _pick_source_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Pick a directory of mixamo FBX files")
        if path:
            self.src_edit.setText(path)
            self.statusBar().showMessage(f"Source dir: {path}")

    def _pick_target(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick the target rig FBX", "", "FBX (*.fbx)")
        if path:
            self.tgt_edit.setText(path)
            self.statusBar().showMessage(f"Target: {path}")

    def _pick_ma(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick the ADV/mGear .ma file (Maya scene)",
            "", "Maya ASCII (*.ma);;All files (*.*)")
        if path:
            self.ma_edit.setText(path)
            self.statusBar().showMessage(f"ADV .ma: {path}")

    def _pick_out_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Pick an output directory")
        if path:
            self.out_edit.setText(path)
            self.statusBar().showMessage(f"Output dir: {path}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _source_paths(self) -> List[str]:
        """Return the list of FBX files the user wants to process."""
        src = self.src_edit.text().strip()
        if not src:
            return []
        if os.path.isdir(src):
            return sorted(
                os.path.join(src, f) for f in os.listdir(src)
                if f.lower().endswith(".fbx")
            )
        if os.path.isfile(src) and src.lower().endswith(".fbx"):
            return [src]
        return []

    def _out_path_for(self, source_fbx: str) -> str:
        out_dir = self.out_edit.text().strip()
        if not out_dir:
            out_dir = os.path.join(os.path.dirname(source_fbx), "_retargeted")
        os.makedirs(out_dir, exist_ok=True)
        name = os.path.splitext(os.path.basename(source_fbx))[0]
        ext = ".ma" if self.mode_maya.isChecked() else ".fbx"
        suffix = "_baked" if self.mode_maya.isChecked() else "_retarget"
        return os.path.join(out_dir, name + suffix + ext)

    # ------------------------------------------------------------------
    # Manual matcher dialog
    # ------------------------------------------------------------------

    def _open_matcher(self) -> None:
        try:
            self._open_matcher_impl()
        except Exception as exc:
            QMessageBox.critical(self, "无法打开匹配面板", str(exc))

    def _open_matcher_impl(self) -> None:
        sources = self._source_paths()
        if not sources:
            QMessageBox.warning(self, "No source",
                                "Pick a source FBX or folder first.")
            return

        try:
            src_doc = load(sources[0])
        except FbxUnavailable as e:
            QMessageBox.critical(self, "Maya runtime error", str(e))
            return

        target_bones: List[str]
        if self.mode_maya.isChecked():
            ma = self.ma_edit.text().strip()
            if ma and os.path.exists(ma):
                from ..maya_driver import list_adv_controllers_from_ma
                target_bones = list_adv_controllers_from_ma(ma)
            else:
                try:
                    from maya import cmds  # type: ignore
                    target_bones = cmds.ls(type="transform", long=True) or []
                except ImportError:
                    QMessageBox.critical(
                        self, "Maya not importable",
                        "Maya mode requires either an ADV .ma file path "
                        "or running inside Maya / over commandPort.")
                    return
        else:
            tgt = self.tgt_edit.text().strip()
            if not tgt or not os.path.exists(tgt):
                QMessageBox.warning(self, "No target",
                                    "Pick a target FBX first.")
                return
            target_bones = self._get_fbx_target_bones(tgt)

        if self._manual_map is None:
            matcher = auto_match_controllers if self.mode_maya.isChecked() else auto_match_adv
            bm = matcher(src_doc.skeleton.bones.keys(), target_bones)
        else:
            bm = self._manual_map
        bm.target_signature = skeleton_signature(target_bones)

        from .matcher_dialog import MatcherDialog
        dlg = MatcherDialog(bm, target_bones, self)
        if dlg.exec() == MatcherDialog.DialogCode.Accepted:
            self._manual_map = dlg.result_map()
            n = sum(1 for e in self._manual_map.entries.values()
                    if e.is_active())
            self.statusBar().showMessage(
                f"Mapping locked: {n} bones active.")

            # Cache the map next to the source so the next run picks it
            # up automatically.
            if self.cache_chk.isChecked():
                cache_path = self._cache_path()
                if cache_path:
                    try:
                        save_bone_map(self._manual_map, cache_path)
                        self.statusBar().showMessage(
                            f"Mapping locked ({n} bones).  "
                            f"Cached at {cache_path}")
                    except OSError as e:
                        self.statusBar().showMessage(
                            f"Mapping locked ({n} bones).  "
                            f"Cache failed: {e}")

    def _cache_path(self) -> Optional[str]:
        """Pick a stable location for the cached bone map."""
        src = self.src_edit.text().strip()
        if not src:
            return None
        base = os.path.dirname(src) if os.path.isfile(src) else src.rstrip("\\/")
        import hashlib
        identity = ("maya" if self.mode_maya.isChecked() else "fbx") + os.path.abspath(self.ma_edit.text() if self.mode_maya.isChecked() else self.tgt_edit.text())
        suffix = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]
        return os.path.join(base, "_mapping_" + suffix + ".json")

    def _load_cached_map(self) -> Optional[BoneMap]:
        from ..retarget_tool import load_bone_map
        cache = self._cache_path()
        if cache and os.path.exists(cache):
            if self.mode_maya.isChecked():
                from ..maya_driver import list_adv_controllers_from_ma
                ma = self.ma_edit.text().strip()
                if not ma:
                    return None
                names = list_adv_controllers_from_ma(ma)
            else:
                target = self.tgt_edit.text().strip()
                if not target or not os.path.exists(target):
                    return None
                names = self._get_fbx_target_bones(target)
            return load_bone_map(cache, skeleton_signature(names))
        return None

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    def _on_generate(self) -> None:
        if not self.gen_btn.isEnabled():
            return
        self.gen_btn.setEnabled(False)
        try:
            self._on_generate_impl()
        except Exception as exc:
            QMessageBox.critical(self, "生成失败", str(exc))
        finally:
            self.gen_btn.setEnabled(True)
            self.progress.setVisible(False)

    def _on_generate_impl(self) -> None:
        sources = self._source_paths()
        if not sources:
            QMessageBox.warning(self, "No source",
                                "Pick a source FBX or folder of FBX files.")
            return

        # Cache hot-load: if a previous mapping exists, reuse it.
        # We only auto-load the cache in modes that actually need a
        # manual map (B and C); mode A is fully automatic.
        if (self._manual_map is None
                and (self.mode_custom.isChecked()
                     or self.mode_maya.isChecked())):
            cached = self._load_cached_map()
            if cached is not None:
                self._manual_map = cached
                self.statusBar().showMessage("Loaded cached mapping.")

        start = self.start_sb.value() if self.start_sb.value() != self._AUTO_FRAME else None
        end = self.end_sb.value() if self.end_sb.value() != self._AUTO_FRAME else None

        self.progress.setVisible(True)
        self.progress.setRange(0, len(sources))
        self.progress.setValue(0)

        results: List[RetargetResult] = []
        errors: List[str] = []

        self.gen_btn.setEnabled(False)
        for i, src in enumerate(sources, 1):
            self.statusBar().showMessage(
                f"[{i}/{len(sources)}] {os.path.basename(src)}")
            QApplication.processEvents()
            try:
                r = self._run_one(src, start, end)
                results.append(r)
                if r.mode == "maya":
                    self.statusBar().showMessage(
                        f"[{i}/{len(sources)}] {os.path.basename(src)}: "
                        f"drove {r.bones_driven} bones.")
                else:
                    self.statusBar().showMessage(
                        f"[{i}/{len(sources)}] {os.path.basename(src)}: "
                        f"saved {r.output_path}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{os.path.basename(src)}: {e}")
                self.statusBar().showMessage(
                    f"[{i}/{len(sources)}] {os.path.basename(src)}: ERROR {e}")
            self.progress.setValue(i)
            QApplication.processEvents()

        # Summary
        ok = sum(1 for r in results
                 if (r.output_path and os.path.exists(r.output_path))
                 or (r.mode == "maya" and not self.ma_edit.text().strip() and r.key_count > 0))
        summary = (f"Processed {len(results)} file(s).  "
                   f"OK: {ok}   Errors: {len(errors)}\n\n")
        if errors:
            summary += "Errors:\n" + "\n".join(errors[:20])
        if len(errors) > 20:
            summary += f"\n... and {len(errors) - 20} more"
        if ok and not errors:
            QMessageBox.information(self, "Done", summary)
        elif ok and errors:
            QMessageBox.warning(self, "Done with errors", summary)
        else:
            QMessageBox.critical(self, "Failed", summary)
        self.progress.setVisible(False)
        self.gen_btn.setEnabled(True)

    def _run_one(self, src: str, start, end) -> RetargetResult:
        if self.mode_adv.isChecked():
            tgt = self.tgt_edit.text().strip()
            if not tgt or not os.path.exists(tgt):
                raise FileNotFoundError(f"Target FBX not found: {tgt}")
            out = self._out_path_for(src)
            return retarget_fbx_adv(src, tgt, out,
                                    start_frame=start, end_frame=end,contact_compensation=self.contact_chk.isChecked())

        if self.mode_custom.isChecked():
            if self._manual_map is None:
                raise RuntimeError(
                    "Click 'Match bones…' first (or load a cached mapping).")
            tgt = self.tgt_edit.text().strip()
            if not tgt or not os.path.exists(tgt):
                raise FileNotFoundError(f"Target FBX not found: {tgt}")
            out = self._out_path_for(src)
            return retarget_fbx_custom(src, tgt, out,
                                       bone_map=self._manual_map,
                                       start_frame=start, end_frame=end,contact_compensation=self.contact_chk.isChecked())

        # mode_maya
        ma = self.ma_edit.text().strip()
        ma_target = ma if ma and os.path.exists(ma) else None
        if self._manual_map is None:
            if ma_target:
                from ..maya_driver import auto_map_from_ma
                src_doc = load(src)
                bm = BoneMap.from_mixamo_names(src_doc.skeleton.bones.keys())
                for mixamo, fk in auto_map_from_ma(ma_target).items():
                    if mixamo in bm.entries:
                        bm.entries[mixamo].target = fk
                        bm.entries[mixamo].enabled = True
                if bm.active_targets():
                    self._manual_map = bm
            elif not self.cp_chk.isChecked():
                try:
                    from maya import cmds  # type: ignore
                    target_bones = cmds.ls(type="transform", long=True) or []
                except ImportError:
                    target_bones = []
                if target_bones:
                    src_doc = load(src)
                    bm = auto_match_controllers(
                        src_doc.skeleton.bones.keys(), target_bones)
                    if bm.active_targets():
                        self._manual_map = bm

        save_ma_path = (
            self._out_path_for(src)
            if (ma_target or self.out_edit.text().strip())
            else None
        )
        return retarget_maya_scene(
            source_fbx=src, bone_map=self._manual_map,
            start_frame=start, end_frame=end,
            use_command_port=self.cp_chk.isChecked(),
            host=self.host_edit.text(),
            port=self.port_edit.value(),
            force_fk=self.fk_chk.isChecked(),
            legs_ik=self.legs_ik_chk.isChecked(),
            contact_compensation=self.contact_chk.isChecked(),
            ma_target=ma_target,
            save_ma_path=save_ma_path,
        )


_APP = None
_WINDOW = None


def main_window() -> RetargetWindow:
    global _APP, _WINDOW
    _APP = QApplication.instance() or QApplication(sys.argv)
    _WINDOW = RetargetWindow()
    _WINDOW.show()
    return _WINDOW


if __name__ == "__main__":  # pragma: no cover
    main_window()
    sys.exit(QApplication.instance().exec())

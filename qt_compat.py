"""Use the Qt binding bundled with Maya 2025 or Maya 2023."""
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError as exc:
        raise RuntimeError('需要 PySide6，或用 Maya 附带的 mayapy 启动界面。') from exc

for cls in (QtWidgets.QApplication, QtWidgets.QDialog):
    if not hasattr(cls, 'exec'):
        cls.exec = cls.exec_

#!/usr/bin/env pythonw
"""
ODB++ HTML5 Standalone Viewer Builder - Qt6 Desktop GUI.
Provides a modern, high-performance interface for configuring and compiling
interactive standalone ODB++ HTML viewers.
Supports both PyQt6 and PySide6 seamlessly.
"""

import os
import sys
import subprocess

# --- Qt Framework Dual-Compatibility Layer ---
try:
    from PyQt6.QtCore import Qt, QThread, pyqtSignal as Signal, QUrl
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QLineEdit, QPushButton, QFileDialog, QComboBox,
        QCheckBox, QTextEdit, QProgressBar, QGroupBox, QFrame, QMessageBox
    )
    from PyQt6.QtGui import (
        QFont, QColor, QDesktopServices, QDragEnterEvent, QDropEvent
    )
    QT_BACKEND = "PyQt6"
except ImportError:
    from PySide6.QtCore import Qt, QThread, Signal, QUrl
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QLineEdit, QPushButton, QFileDialog, QComboBox,
        QCheckBox, QTextEdit, QProgressBar, QGroupBox, QFrame, QMessageBox
    )
    from PySide6.QtGui import (
        QFont, QColor, QDesktopServices, QDragEnterEvent, QDropEvent
    )
    QT_BACKEND = "PySide6"

# Import core compiler functions
import odb_viewer_compiler as compiler


# --- Background Compilation Worker Thread ---
class CompileWorker(QThread):
    log_signal = Signal(str)
    progress_signal = Signal(int)
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, odb_input, output_path, step, include_inner):
        super().__init__()
        self.odb_input = odb_input
        self.output_path = output_path
        self.step = step
        self.include_inner = include_inner

    def run(self):
        try:
            self.progress_signal.emit(10)
            self.log_signal.emit(f"Starting compilation of: {self.odb_input}")
            self.log_signal.emit(f"Target step: {self.step if self.step else 'Auto (Primary)'}")

            def worker_log(msg):
                self.log_signal.emit(msg)

            self.progress_signal.emit(35)
            meta = compiler.compile_odb_to_html(
                odb_input=self.odb_input,
                output_path=self.output_path,
                step=self.step,
                include_inner=self.include_inner,
                log_fn=worker_log
            )
            self.progress_signal.emit(100)
            self.finished_signal.emit(meta)
        except Exception as e:
            import traceback
            err_msg = traceback.format_exc()
            self.error_signal.emit(err_msg)


# --- Modern Dark Theme Stylesheet ---
DARK_STYLESHEET = """
QMainWindow {
    background-color: #0d1117;
}

QWidget {
    color: #c9d1d9;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Segoe UI Variable", Roboto, sans-serif;
    font-size: 13px;
}

QGroupBox {
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-top: 18px;
    padding: 14px 10px 10px 10px;
    background-color: #161b22;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    top: 6px;
    padding: 0 6px;
    color: #58a6ff;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

QLineEdit {
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 8px 10px;
    color: #f0f6fc;
    selection-background-color: #1f6feb;
}

QLineEdit:focus {
    border: 1px solid #58a6ff;
    background-color: #010409;
}

QLineEdit:hover {
    border: 1px solid #8b949e;
}

QPushButton {
    background-color: #21262d;
    border: 1px solid #363b42;
    border-radius: 6px;
    color: #c9d1d9;
    padding: 8px 14px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #30363d;
    border-color: #8b949e;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #161b22;
}

QPushButton:disabled {
    background-color: #161b22;
    border-color: #21262d;
    color: #484f58;
}

QPushButton#btn_primary {
    background-color: #238636;
    border: 1px solid #2ea043;
    color: #ffffff;
    font-weight: 600;
    font-size: 14px;
    padding: 10px 20px;
}

QPushButton#btn_primary:hover {
    background-color: #2ea043;
    border-color: #3fb950;
}

QPushButton#btn_primary:pressed {
    background-color: #196c2e;
}

QPushButton#btn_primary:disabled {
    background-color: #21262d;
    border-color: #30363d;
    color: #484f58;
}

QPushButton#btn_action {
    background-color: #1f6feb;
    border: 1px solid #388bfd;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#btn_action:hover {
    background-color: #388bfd;
}

QComboBox {
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 8px 10px;
    color: #f0f6fc;
}

QComboBox:focus {
    border: 1px solid #58a6ff;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 25px;
    border-left: 1px solid #30363d;
}

QComboBox QAbstractItemView {
    background-color: #161b22;
    border: 1px solid #30363d;
    selection-background-color: #1f6feb;
    selection-color: #ffffff;
    color: #f0f6fc;
}

QCheckBox {
    spacing: 8px;
    color: #c9d1d9;
    font-weight: 500;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #30363d;
    background-color: #0d1117;
}

QCheckBox::indicator:hover {
    border-color: #58a6ff;
}

QCheckBox::indicator:checked {
    background-color: #1f6feb;
    border-color: #58a6ff;
}

QTextEdit#console_log {
    background-color: #010409;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: #58a6ff;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    line-height: 1.4;
    padding: 8px;
}

QProgressBar {
    border: 1px solid #30363d;
    border-radius: 4px;
    text-align: center;
    background-color: #0d1117;
    color: #ffffff;
    height: 14px;
}

QProgressBar::chunk {
    background-color: #238636;
    border-radius: 3px;
}

QLabel#title_label {
    font-size: 18px;
    font-weight: 700;
    color: #f0f6fc;
}

QLabel#subtitle_label {
    font-size: 12px;
    color: #8b949e;
}

QLabel#drop_badge {
    color: #58a6ff;
    font-size: 11px;
    font-weight: 600;
    background-color: rgba(56, 139, 253, 0.12);
    border: 1px dashed rgba(56, 139, 253, 0.4);
    border-radius: 6px;
    padding: 8px;
    text-align: center;
}
"""


class OdbViewerGui(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"ODB++ Standalone HTML Viewer Builder [{QT_BACKEND}]")
        self.resize(760, 680)
        self.setMinimumSize(640, 560)
        self.setAcceptDrops(True)
        self.setStyleSheet(DARK_STYLESHEET)

        self.last_generated_file = None
        self.worker = None

        self._build_ui()

    def _build_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # 1. Header Banner
        header_layout = QHBoxLayout()
        title_box = QVBoxLayout()
        title_lbl = QLabel("ODB++ Standalone HTML Viewer Builder", self)
        title_lbl.setObjectName("title_label")
        subtitle_lbl = QLabel("Compile ODB++ archives into zero-dependency, ultra-fast 60 FPS HTML5 Canvas viewers", self)
        subtitle_lbl.setObjectName("subtitle_label")
        title_box.addWidget(title_lbl)
        title_box.addWidget(subtitle_lbl)
        header_layout.addLayout(title_box)
        header_layout.addStretch()

        badge_lbl = QLabel("Drag & Drop Archive Here", self)
        badge_lbl.setObjectName("drop_badge")
        header_layout.addWidget(badge_lbl)
        main_layout.addLayout(header_layout)

        # 2. Source Input Group
        input_group = QGroupBox("1. Input ODB++ Source (.zip, .tgz, .tar.gz, folder)", self)
        input_layout = QVBoxLayout(input_group)
        input_layout.setSpacing(8)

        input_row = QHBoxLayout()
        self.txt_input = QLineEdit(self)
        self.txt_input.setPlaceholderText("Select ODB++ archive or folder, or drag and drop a file...")
        self.txt_input.textChanged.connect(self._on_input_changed)
        input_row.addWidget(self.txt_input)

        btn_browse_archive = QPushButton("Browse Archive...", self)
        btn_browse_archive.clicked.connect(self._browse_archive)
        input_row.addWidget(btn_browse_archive)

        btn_browse_folder = QPushButton("Browse Folder...", self)
        btn_browse_folder.clicked.connect(self._browse_folder)
        input_row.addWidget(btn_browse_folder)
        input_layout.addLayout(input_row)

        main_layout.addWidget(input_group)

        # 3. Step & Options Configuration Group
        options_group = QGroupBox("2. Step & Configuration Options", self)
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(10)

        # Step selection row
        step_row = QHBoxLayout()
        step_lbl = QLabel("Target Step:", self)
        step_lbl.setFixedWidth(90)
        self.combo_step = QComboBox(self)
        self.combo_step.addItem("(Auto Detect Primary Step)")
        step_row.addWidget(step_lbl)
        step_row.addWidget(self.combo_step)

        btn_detect_steps = QPushButton("Detect Steps", self)
        btn_detect_steps.clicked.connect(self._detect_steps)
        step_row.addWidget(btn_detect_steps)
        options_layout.addLayout(step_row)

        # Toggles row
        toggles_row = QHBoxLayout()
        self.chk_inner = QCheckBox("Include Inner Copper Layers", self)
        self.chk_inner.setToolTip("Include inner signal copper layers (larger file size)")
        toggles_row.addWidget(self.chk_inner)

        self.chk_auto_open = QCheckBox("Auto-open in Browser on Completion", self)
        self.chk_auto_open.setChecked(True)
        toggles_row.addWidget(self.chk_auto_open)
        toggles_row.addStretch()
        options_layout.addLayout(toggles_row)

        main_layout.addWidget(options_group)

        # 4. Output Path Group
        output_group = QGroupBox("3. Output Standalone HTML File", self)
        output_layout = QHBoxLayout(output_group)
        self.txt_output = QLineEdit(self)
        self.txt_output.setPlaceholderText("Output HTML file path (e.g. viewer_board.html)...")
        output_layout.addWidget(self.txt_output)

        btn_browse_out = QPushButton("Browse...", self)
        btn_browse_out.clicked.connect(self._browse_output)
        output_layout.addWidget(btn_browse_out)
        main_layout.addWidget(output_group)

        # 5. Build Actions & Progress
        actions_row = QHBoxLayout()
        self.btn_compile = QPushButton("Generate HTML Viewer", self)
        self.btn_compile.setObjectName("btn_primary")
        self.btn_compile.clicked.connect(self._start_compilation)
        actions_row.addWidget(self.btn_compile)

        self.btn_open_browser = QPushButton("Open in Browser", self)
        self.btn_open_browser.setObjectName("btn_action")
        self.btn_open_browser.setEnabled(False)
        self.btn_open_browser.clicked.connect(self._open_in_browser)
        actions_row.addWidget(self.btn_open_browser)

        self.btn_reveal_folder = QPushButton("Reveal in Explorer", self)
        self.btn_reveal_folder.setEnabled(False)
        self.btn_reveal_folder.clicked.connect(self._reveal_in_folder)
        actions_row.addWidget(self.btn_reveal_folder)
        main_layout.addLayout(actions_row)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # 6. Compilation Log Terminal
        log_group = QGroupBox("Compilation Output & Diagnostics", self)
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 12, 8, 8)
        self.console_log = QTextEdit(self)
        self.console_log.setObjectName("console_log")
        self.console_log.setReadOnly(True)
        log_layout.addWidget(self.console_log)
        main_layout.addWidget(log_group)

        self._log(f"Ready. Detected Qt framework: {QT_BACKEND}")

    # --- Drag & Drop Support ---
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if file_path:
                self.txt_input.setText(file_path)
                event.acceptProposedAction()

    # --- Event Handlers & Helpers ---
    def _log(self, text, color=None):
        if color:
            self.console_log.append(f'<span style="color: {color};">{text}</span>')
        else:
            self.console_log.append(text)
        self.console_log.ensureCursorVisible()

    def _browse_archive(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select ODB++ Archive",
            "",
            "ODB++ Archives (*.zip *.tgz *.tar.gz *.tar);;All Files (*.*)"
        )
        if filename:
            self.txt_input.setText(filename)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select ODB++ Extracted Folder")
        if folder:
            self.txt_input.setText(folder)

    def _browse_output(self):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output HTML File",
            self.txt_output.text() or "pcb_viewer.html",
            "HTML Documents (*.html *.htm);;All Files (*.*)"
        )
        if filename:
            self.txt_output.setText(filename)

    def _on_input_changed(self, text):
        path = text.strip()
        if not path or not os.path.exists(path):
            return

        # Auto-derive output HTML name if not manually modified
        dirname = os.path.dirname(os.path.abspath(path))
        base_name = os.path.basename(path)
        for ext in (".zip", ".tgz", ".tar.gz", ".tar"):
            if base_name.lower().endswith(ext):
                base_name = base_name[:-len(ext)]
                break
        suggested_out = os.path.join(dirname, f"viewer_{base_name}.html")
        if not self.txt_output.text() or self.txt_output.text().endswith("viewer.html") or "viewer_" in self.txt_output.text():
            self.txt_output.setText(suggested_out)

        # Trigger auto step detection
        self._detect_steps()

    def _detect_steps(self):
        path = self.txt_input.text().strip()
        if not path or not os.path.exists(path):
            return

        try:
            primary_step, step_list = compiler.get_available_steps(path)
            self.combo_step.clear()
            self.combo_step.addItem("(Auto Detect Primary Step)", None)

            selected_idx = 0
            for i, s in enumerate(step_list):
                self.combo_step.addItem(s, s)
                if s == primary_step:
                    selected_idx = i + 1

            if selected_idx == 0:
                for i, s in enumerate(step_list):
                    if s.lower() == primary_step.lower():
                        selected_idx = i + 1
                        break

            self.combo_step.setCurrentIndex(selected_idx)
            self._log(f"Detected steps in archive: {step_list} (Recommended: '{primary_step}')", "#7ee787")
        except Exception as e:
            self._log(f"Could not scan steps: {e}", "#ff7b72")

    def _start_compilation(self):
        odb_path = self.txt_input.text().strip()
        if not odb_path or not os.path.exists(odb_path):
            QMessageBox.critical(self, "Invalid Input", "Please select a valid ODB++ archive or folder.")
            return

        out_path = self.txt_output.text().strip()
        if not out_path:
            QMessageBox.critical(self, "Invalid Output", "Please specify an output HTML file path.")
            return

        step = self.combo_step.currentData()
        include_inner = self.chk_inner.isChecked()

        # UI state during compilation
        self.btn_compile.setEnabled(False)
        self.btn_open_browser.setEnabled(False)
        self.btn_reveal_folder.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(15)
        self.console_log.clear()
        self._log(f"--- Compilation Started ---", "#58a6ff")

        # Launch QThread worker
        self.worker = CompileWorker(
            odb_input=odb_path,
            output_path=out_path,
            step=step,
            include_inner=include_inner
        )
        self.worker.log_signal.connect(self._on_worker_log)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self._on_worker_finished)
        self.worker.error_signal.connect(self._on_worker_error)
        self.worker.start()

    def _on_worker_log(self, msg):
        if "SUCCESS" in msg:
            self._log(msg, "#3fb950")
        elif "WARNING" in msg or "Error" in msg:
            self._log(msg, "#f0883e")
        else:
            self._log(msg)

    def _on_worker_finished(self, meta):
        self.btn_compile.setEnabled(True)
        self.progress_bar.setValue(100)
        out_file = meta.get("output_path", "")
        self.last_generated_file = out_file
        self.btn_open_browser.setEnabled(True)
        self.btn_reveal_folder.setEnabled(True)

        size_mb = meta.get("size_mb", 0.0)
        comps = meta.get("components_count", 0)
        nets = meta.get("nets_count", 0)
        layers = meta.get("layers_count", 0)

        self._log(f"Compilation Complete! Size: {size_mb:.2f} MB | {comps} Comps | {nets} Nets | {layers} Layers", "#3fb950")

        if self.chk_auto_open.isChecked() and os.path.exists(out_file):
            self._open_in_browser()

    def _on_worker_error(self, err):
        self.btn_compile.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._log(f"Compilation Failed:\n{err}", "#ff7b72")
        QMessageBox.critical(self, "Compilation Error", f"An error occurred during compilation:\n{err}")

    def _open_in_browser(self):
        if self.last_generated_file and os.path.exists(self.last_generated_file):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_generated_file))
        elif self.txt_output.text() and os.path.exists(self.txt_output.text()):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(self.txt_output.text())))

    def _reveal_in_folder(self):
        target = self.last_generated_file or self.txt_output.text()
        if target and os.path.exists(target):
            if sys.platform == "win32":
                subprocess.run(["explorer", "/select,", os.path.abspath(target)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(os.path.abspath(target))))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ODB++ HTML Viewer Builder")
    window = OdbViewerGui()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

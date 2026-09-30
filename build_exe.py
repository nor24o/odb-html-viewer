#!/usr/bin/env python3
"""
Automated PyInstaller build script for ODB++ Standalone HTML Viewer Builder.
Packages `odb_viewer_gui.pyw` into a standalone Windows .exe with no console window.
"""

import os
import sys
import subprocess
import shutil

def build_executable():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    entry_script = os.path.join(root_dir, "odb_viewer_gui.pyw")
    exe_name = "ODB_Viewer_Builder"

    print(f"Building standalone Windows executable for {entry_script}...")

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--name", exe_name,
        "--onefile",
        "--windowed",
        "--clean",
        "--noconfirm",
        "--exclude-module", "PySide6",
        "--exclude-module", "shiboken6",
        "--add-data", f"{os.path.join(root_dir, 'odb_viewer_compiler.py')}{os.pathsep}.",
        entry_script
    ]

    print("Running command:", " ".join(cmd))
    res = subprocess.run(cmd, cwd=root_dir)

    if res.returncode != 0:
        print(f"ERROR: PyInstaller build failed with code {res.returncode}", file=sys.stderr)
        sys.exit(res.returncode)

    dist_dir = os.path.join(root_dir, "dist")
    exe_path = os.path.join(dist_dir, f"{exe_name}.exe")

    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print("\n" + "=" * 60)
        print("BUILD SUCCESSFUL!")
        print(f"Output Executable: {exe_path}")
        print(f"Executable Size:   {size_mb:.2f} MB")
        print("=" * 60 + "\n")
    else:
        print(f"ERROR: Expected executable not found at {exe_path}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    build_executable()

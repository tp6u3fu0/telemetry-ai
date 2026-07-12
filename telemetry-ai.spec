# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包設定：Telemetry AI 桌面版（Windows, onefile）。

建置：
    uv run --with pyinstaller pyinstaller telemetry-ai.spec
產出：dist/Telemetry-AI.exe（單一檔，可直接雙擊）

要點：
  * 入口 webapp/desktop.py（Flask 背景 thread + pywebview WebView2 視窗）
  * 帶入唯讀資源：webapp/static（前端）、data/tracks（彎道對照表）
  * 排除 matplotlib/tkinter（執行期用不到，省 ~40MB）
  * irsdk 延遲載入，PyInstaller 靜態分析抓不到 → 手動 hidden import
  * pywebview 的 JS 資源與平台後端一併收集
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("webapp/static", "webapp/static"),
    ("data/tracks", "data/tracks"),
]
datas += collect_data_files("webview")        # pywebview 內建 JS

hiddenimports = ["irsdk"]
hiddenimports += collect_submodules("anthropic")
hiddenimports += collect_submodules("webview")  # 平台後端（edgechromium 等）

a = Analysis(
    ["run_app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Telemetry-AI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX 常誤觸防毒；關閉較保險
    runtime_tmpdir=None,
    console=False,            # 無主控台視窗（純 GUI）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

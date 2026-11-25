from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, get_package_paths

root = Path.cwd()
repo_root = root.parent
site = repo_root / ".venv"
hyperscan_base, _ = get_package_paths("hyperscan")
hyperscan_libs = Path(hyperscan_base) / "hyperscan.libs"
extra_datas = (
    collect_data_files("webview")
    + collect_data_files("pythonnet")
    + collect_data_files("rapidocr_onnxruntime")
    + collect_data_files("hyperscan")
)
extra_binaries = [
    (str(path), "hyperscan.libs")
    for path in hyperscan_libs.glob("*.dll")
]

a = Analysis(
    ["app/main.py"],
    pathex=[str(root), str(repo_root), str(site)],
    binaries=extra_binaries,
    datas=[
        ("app/assets", "assets"),
        ("resources", "resources"),
    ] + extra_datas,
    hiddenimports=[
        "webview.platforms.winforms",
        "pdf2zh.converter",
        "pdf2zh.high_level",
        "pdf2zh.translator",
        "babeldoc.assets.assets",
        "onnxruntime",
        "cv2",
        "fitz",
        "hyperscan",
        "hyperscan._hs_ext",
        "rapidocr_onnxruntime",
        "babeldoc.docvision.table_detection.rapidocr",
        "babeldoc._rust_il",
        "babeldoc.progress_monitor",
        "babeldoc.docvision.doclayout",
        "babeldoc.format.pdf.high_level",
        "babeldoc.format.pdf.parser_errors",
        "babeldoc.format.pdf.parser_resource",
        "babeldoc.format.pdf.result_merger",
        "babeldoc.format.pdf.split_manager",
        "babeldoc.format.pdf.translation_config",
        "babeldoc.format.pdf.document_il.il_version_1",
        "babeldoc.format.pdf.document_il.backend.pdf_creater",
        "babeldoc.rust_callback_bridge",
        "babeldoc.rust_layout_service",
        "babeldoc.rust_run_context",
        "babeldoc.rust_scan_service",
        "babeldoc.rust_translation_service",
        "babeldoc.rust_writer_service",
        "app.translator",
        "app.translation_worker",
        "app._launch_test",
        "app._bridge_test",
        "clr_loader",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Pdf2zhStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="resources/app.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Pdf2zhStudio",
)


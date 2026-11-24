"""Exercise the desktop bridge without showing a window or opening file dialogs."""

from __future__ import annotations

import atexit
import faulthandler
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback


def run(report_path, timeout=30, model_path=None):
    import webview
    from app import studio as backend
    from app.main import _asset_path

    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "phases": []}
    finished = threading.Event()
    copy_started = threading.Event()
    allow_copy = threading.Event()
    report_lock = threading.Lock()

    def check(condition, message):
        # PyInstaller uses optimize=1, so plain assert statements are stripped.
        if not condition:
            raise AssertionError(message)

    def record(phase, **values):
        with report_lock:
            report["phases"].append(phase)
            report.update(values)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    def watchdog():
        if not finished.wait(timeout):
            record("timeout", error="Desktop bridge did not return")
            os._exit(1)

    trace_file = report_path.with_suffix(".threads.log").open("w", encoding="utf-8")
    faulthandler.dump_traceback_later(timeout - 2, file=trace_file)
    threading.Thread(target=watchdog, daemon=True).start()

    with tempfile.TemporaryDirectory(prefix="bridge-", dir=report_path.parent) as folder:
        root = Path(folder)
        backend.APP_DIR = root / "data"
        backend.MODEL_DIR = backend.APP_DIR / "models"
        backend.CONFIG_PATH = backend.APP_DIR / "settings.json"
        backend.SERVER_PID_PATH = backend.APP_DIR / "llama-server.pid"
        source = root / "bridge-fixture.gguf"
        source.write_bytes(b"GGUF" + b"x" * (4 * 1024 * 1024))
        app = backend.Studio()
        app._output_dir = root / "output"
        window = webview.create_window(
            "Pdf2zh bridge regression test", _asset_path(), js_api=app,
            hidden=True, width=1100, height=750,
        )
        app._window = window
        real_copy = backend.shutil.copy2

        def delayed_copy(src, dst):
            copy_started.set()
            if not allow_copy.wait(10):
                raise TimeoutError("Interface blocked while copying a model")
            return real_copy(src, dst)

        backend.shutil.copy2 = delayed_copy

        def selected_file(*args, **kwargs):
            # Use the real native dispatch, but never display a file picker.
            return app._run_on_ui_thread(lambda: (str(source),))

        app._create_file_dialog = selected_file

        def wait_js(expression, seconds=8):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                value = window.evaluate_js(expression)
                if value:
                    return value
                time.sleep(0.05)
            raise AssertionError("Timed out waiting for " + expression)

        def start_import():
            window.evaluate_js("""
                window.__importResult = null;
                Promise.resolve(document.getElementById('importModel').onclick())
                    .then(() => window.__importResult = 'done')
                    .catch(e => window.__importResult = String(e));
                true;
            """)

        def exercise():
            try:
                if not window.events.loaded.wait(10):
                    raise TimeoutError("Hidden WebView did not load")
                wait_js("!!window.pywebview && !!window.pywebview.api && state.resource !== undefined")
                check(not window.native.Visible, "Test window must remain hidden")
                record("bridge-ready")
                start_import()
                if not copy_started.wait(5):
                    raise TimeoutError("Import did not reach file copy")
                record("copy-in-progress")
                check(window.evaluate_js("document.querySelectorAll('.toast').length") > 0, "Import notification missing")
                check(window.evaluate_js("navigate('settings'); document.getElementById('pageTitle').textContent"), "Navigation blocked")
                check(window.evaluate_js("document.getElementById('importModel').disabled"), "Import button must be disabled")
                check(app.get_state()["models"] == [], "Incomplete model was published")
                record("interface-responsive-during-copy")
                allow_copy.set()
                check(wait_js("window.__importResult") == "done", "Import did not finish")
                wait_js("state.models.length === 1")
                target = backend.MODEL_DIR / source.name
                check(target.read_bytes() == source.read_bytes(), "Imported model differs from source")
                check(not window.evaluate_js("document.getElementById('importModel').disabled"), "Import button stayed disabled")
                record("model-imported-and-listed")

                def broken_copy(src, dst):
                    Path(dst).write_bytes(b"incomplete")
                    raise OSError("Simulated copy failure")

                backend.shutil.copy2 = broken_copy
                start_import()
                check(wait_js("window.__importResult") == "done", "Copy failure did not return")
                check(target.read_bytes() == source.read_bytes(), "Copy failure damaged the original model")
                check(not list(backend.MODEL_DIR.glob("*.part")), "Staging file was not cleaned up")
                check(not window.evaluate_js("document.getElementById('importModel').disabled"), "Failed import left button disabled")
                check(window.evaluate_js("document.getElementById('toastRegion').textContent.includes('Simulated copy failure')"), "Copy error not displayed")
                record("failed-copy-preserves-model-and-restores-button")
                backend.shutil.copy2 = real_copy
                app._create_file_dialog = lambda *args, **kwargs: None
                start_import()
                check(wait_js("window.__importResult") == "done", "Cancelled picker did not return")
                check(not window.evaluate_js("document.getElementById('importModel').disabled"), "Cancelled picker left button disabled")
                record("cancelled-picker-restores-button")

                if model_path:
                    backend.MODEL_DIR = Path(model_path).resolve().parent
                    name = Path(model_path).name
                    app._settings["context"] = 4096
                    started = time.monotonic()
                    window.evaluate_js("""
                        window.__loadDone = false;
                        refresh().then(() => {
                            const button = [...document.querySelectorAll('[data-action=load]')]
                                .find(b => b.dataset.name === %s);
                            if (!button) throw new Error('Test model not listed');
                            return document.getElementById('modelList').onclick({target: button});
                        }).then(() => window.__loadDone = true);
                        true;
                    """ % json.dumps(name))
                    wait_js("window.__loadDone", seconds=65)
                    loaded = app.get_state()
                    check(loaded["loadedModel"] == name, loaded)
                    check(not loaded["loading"] and not loaded["loadingError"], loaded)
                    check(window.evaluate_js("document.getElementById('loadingOverlay').classList.contains('hidden')"), "Loading overlay remained visible")
                    record("real-model-ready-in-interface", model=name,
                           load_seconds=round(time.monotonic() - started, 2))

                    source_pdf = Path("test-sample.pdf").resolve()
                    started = time.monotonic()
                    app.start_translation({
                        "path": str(source_pdf),
                        "sourceLanguage": "en",
                        "language": "zh",
                        "format": "mono",
                        "pages": "",
                        "threads": 1,
                        "ocr": False,
                        "translateTableText": True,
                    })
                    while time.monotonic() - started < 90:
                        current = app.get_state()
                        job = current["jobs"][-1]
                        check(
                            window.evaluate_js(
                                "navigate('settings'); document.getElementById('pageTitle').textContent"
                            ) == "设置",
                            "Interface blocked during translation",
                        )
                        if job["status"] != "running":
                            break
                        time.sleep(0.25)
                    else:
                        raise TimeoutError("Translation process did not finish")
                    check(job["status"] == "completed", job)
                    check(job["outputs"] and Path(job["outputs"][0]["path"]).is_file(), job)
                    record("translation-process-responsive",
                           translation_seconds=round(time.monotonic() - started, 2))

                # Unload and translation use the same event-delivery path.
                window.evaluate_js("window.__unloaded=false; window.pywebview.api.unload_model().then(() => window.__unloaded=true); true;")
                wait_js("window.__unloaded")
                record("unload-notification-returned")
                for status in ("running", "completed", "failed"):
                    job = {"id": "bridge-job", "name": "fixture.pdf", "status": status,
                           "progress": 100 if status == "completed" else 0,
                           "detail": status, "outputs": []}
                    app._emit("job-updated", job)
                    check(window.evaluate_js("state.jobs.find(j => j.id === 'bridge-job').status") == status, "Job event not delivered: " + status)
                record("translation-events-returned")
                check(not window.native.Visible, "Test window became visible")
                record("passed", ok=True)
            except Exception:
                diagnostics = {}
                for name in ("model-loader-error.log", "cuda-server.log"):
                    path = backend.APP_DIR / name
                    if path.exists():
                        diagnostics[name] = path.read_text(encoding="utf-8", errors="replace")[-12000:]
                record("failed", error=traceback.format_exc(), diagnostics=diagnostics)
            finally:
                allow_copy.set()
                app._shutdown_cuda_server()
                window.destroy()

        try:
            record("starting-hidden-webview")
            webview.start(exercise, gui="edgechromium", storage_path=str(root / "webview"))
        finally:
            finished.set()
            faulthandler.cancel_dump_traceback_later()
            trace_file.close()
            backend.shutil.copy2 = real_copy
            atexit.unregister(app._shutdown_cuda_server)
            app._window = None

    if not report["ok"]:
        raise RuntimeError(report.get("error", "Bridge test failed"))
    return report


if __name__ == "__main__":
    run(sys.argv[1])

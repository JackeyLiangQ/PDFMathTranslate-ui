from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import sys
import urllib.error
import urllib.request
import webview
from pathlib import Path


APP_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "Pdf2zhStudio"
MODEL_DIR = APP_DIR / "models"
CONFIG_PATH = APP_DIR / "settings.json"
RESOURCE_DIR = (
    Path(sys._MEIPASS) / "resources"
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[1] / "resources"
)
TESSDATA_DIR = RESOURCE_DIR / "tessdata"
if getattr(sys, "frozen", False):
    os.environ["NOTO_FONT_PATH"] = str(RESOURCE_DIR / "SourceHanSerifCN-Regular.ttf")
    os.environ["HF_HUB_OFFLINE"] = "1"
if any(TESSDATA_DIR.glob("*.traineddata")):
    os.environ.setdefault("TESSDATA_PREFIX", str(TESSDATA_DIR))


class _CudaModelClient:
    def __init__(self, process: subprocess.Popen, base_url: str, log_stream) -> None:
        self._process = process
        self._base_url = base_url.rstrip("/")
        self._log_stream = log_stream

    def create_chat_completion(self, *, messages, temperature=0.15, max_tokens=2048):
        payload = json.dumps({
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"CUDA 推理服务错误：{detail[:500]}") from exc

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        try:
            self._log_stream.close()
        except Exception:
            pass


class Studio:
    def __init__(self) -> None:
        self._window = None
        self._lock = threading.Lock()
        self._model = None
        self._model_path = ""
        self._cuda_server = None
        self._loading = False
        self._workers = None
        self._cancel = threading.Event()
        self._jobs: dict[str, dict] = {}
        self._settings = self._read_settings()
        self._output_dir = Path(self._settings.get("outputDir", Path.home() / "Documents" / "Pdf2zhStudio"))
        self._window_storage = APP_DIR / "webview"
        atexit.register(self._shutdown_cuda_server)

    @staticmethod
    def _read_settings() -> dict:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"language": "zh", "format": "dual", "threads": 2,
                    "context": 4096, "ocr": True, "translateTableText": True}

    def _save_settings(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self._settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run_on_ui_thread(self, callback):
        """Run native WinForms actions on the thread that owns the window."""
        native = getattr(self._window, "native", None)
        if native is None or not native.InvokeRequired:
            return callback()

        import clr
        from System import Func, Object

        return native.Invoke(Func[Object](callback))

    def _create_file_dialog(self, dialog_type, file_types=()):
        return self._run_on_ui_thread(
            lambda: self._window.gui.create_file_dialog(
                dialog_type, "", False, "", file_types, self._window.uid
            )
        )

    def get_state(self) -> dict:
        import psutil

        models = []
        if MODEL_DIR.exists():
            for path in MODEL_DIR.glob("*.gguf"):
                models.append({"name": path.name, "size": path.stat().st_size, "path": str(path)})
        return {
            "models": models,
            "loadedModel": Path(self._model_path).name if self._model_path else None,
            "device": "CUDA" if self._cuda_server else "CPU",
            "loading": self._loading, "settings": self._settings,
            "outputDir": str(self._output_dir), "jobs": list(self._jobs.values()),
            "resource": {"ram": round(psutil.virtual_memory().available / 1024**3, 1), "cores": os.cpu_count() or 4},
        }

    def pick_model(self) -> dict | None:
        selected = self._create_file_dialog(
            webview.OPEN_DIALOG, ("GGUF 模型 (*.gguf)",)
        )
        return self.import_model(selected[0]) if selected else None

    def import_model(self, source: str) -> dict:
        source_path = Path(source)
        if source_path.suffix.lower() != ".gguf" or not source_path.is_file():
            raise ValueError("请选择有效的 .gguf 模型文件。")
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        target = MODEL_DIR / source_path.name
        if source_path.resolve() != target.resolve():
            self._notify("正在导入模型", "大文件复制期间可以继续浏览界面。", "info")
            shutil.copy2(source_path, target)
        return {"name": target.name, "size": target.stat().st_size, "path": str(target)}

    def remove_model(self, name: str) -> dict:
        if self._model_path and Path(self._model_path).name == name:
            raise RuntimeError("请先卸载正在使用的模型。")
        target = (MODEL_DIR / name).resolve()
        if target.parent != MODEL_DIR.resolve() or target.suffix != ".gguf":
            raise ValueError("模型路径无效。")
        target.unlink(missing_ok=True)
        return self.get_state()

    def load_model(self, name: str, context: int = 4096) -> dict:
        with self._lock:
            if self._model is not None or self._loading:
                raise RuntimeError("已有模型正在加载或运行，请先卸载。")
            self._loading = True
        path = (MODEL_DIR / name).resolve()
        if path.parent != MODEL_DIR.resolve() or not path.is_file():
            self._loading = False
            raise ValueError("找不到所选模型。")
        self._settings["context"] = int(context)
        self._save_settings()
        threading.Thread(target=self._load_worker, args=(path, int(context)), daemon=True).start()
        return {"loading": True}

    def _load_worker(self, path: Path, context: int) -> None:
        cuda_server = None
        try:
            from pdf2zh.doclayout import OnnxModel

            if getattr(sys, "frozen", False):
                layout_path = Path(sys._MEIPASS) / "resources" / "doclayout.onnx"
                OnnxModel.from_pretrained = staticmethod(lambda: OnnxModel(str(layout_path)))
            cuda_server = self._start_cuda_server(path, context)
            self._cuda_server = cuda_server
            self._model = cuda_server
            self._model_path = str(path)
            self._notify("模型已就绪", f"{path.name} 已加载，可以开始本地翻译。", "success")
        except Exception as exc:
            if cuda_server is not None:
                cuda_server.close()
            self._notify("模型加载失败", str(exc), "error")
        finally:
            self._loading = False
            self._emit("model-state", {"loaded": bool(self._model), "loading": False})
            if not self._model:
                self._notify("模型未加载", "请检查文件格式与可用内存后重试。", "error")

    def unload_model(self) -> dict:
        if self._workers:
            raise RuntimeError("翻译进行中，不能卸载模型。")
        self._shutdown_cuda_server()
        self._model = None
        self._model_path = ""
        self._notify("模型已卸载", "内存已释放。", "success")
        return {"loadedModel": None}

    @staticmethod
    def _cuda_backend_dir() -> Path | None:
        app_root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        candidates = [app_root / "resources" / "cuda-backend"]
        configured = os.getenv("PDF2ZH_CUDA_BACKEND", "").strip()
        if configured:
            candidates.insert(0, Path(configured))
        candidates.extend([
            Path(r"D:\Program\LM Studio\resources\app\.webpack\bin\extensions\backends")
            / "llama.cpp-win-x86_64-nvidia-cuda-avx2-2.54.0",
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "LM Studio"
            / "resources" / "app" / ".webpack" / "bin" / "extensions" / "backends"
            / "llama.cpp-win-x86_64-nvidia-cuda-avx2-2.54.0",
        ])
        for candidate in candidates:
            server = candidate / "bin" / "llama-server.exe"
            if not server.is_file():
                server = candidate / "llama-server.exe"
            if server.is_file():
                return candidate
        return None

    def _start_cuda_server(self, path: Path, context: int) -> _CudaModelClient:
        backend = self._cuda_backend_dir()
        if backend is None:
            raise RuntimeError("未找到 CUDA 后端，请确认已打包 cuda-backend 文件。")
        server = backend / "bin" / "llama-server.exe"
        if not server.is_file():
            server = backend / "llama-server.exe"
        vendor = backend / "vendor"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        APP_DIR.mkdir(parents=True, exist_ok=True)
        log_stream = (APP_DIR / "cuda-server.log").open("ab")
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join(str(item) for item in (server.parent, vendor, env.get("PATH", "")) if item)
        command = [
            str(server), "-m", str(path), "--host", "127.0.0.1", "--port", str(port),
            "--gpu-layers", "all", "--device", "CUDA0", "--ctx-size", str(context),
            "--threads", str(max(1, min((os.cpu_count() or 4) - 1, 12))),
            "--no-webui", "--reasoning", "off",
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command, cwd=str(server.parent), env=env, stdin=subprocess.DEVNULL,
            stdout=log_stream, stderr=subprocess.STDOUT, creationflags=creation_flags,
        )
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log_stream.close()
                raise RuntimeError("CUDA 后端启动失败，请查看 Pdf2zhStudio/cuda-server.log。")
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=2) as response:
                    if response.status == 200:
                        return _CudaModelClient(process, base_url, log_stream)
            except (OSError, urllib.error.URLError):
                time.sleep(1)
        process.terminate()
        log_stream.close()
        raise RuntimeError("CUDA 模型加载超时，请检查显存或 cuda-server.log。")

    def _shutdown_cuda_server(self) -> None:
        server = self._cuda_server
        self._cuda_server = None
        if server is not None:
            server.close()

    def pick_pdf(self) -> dict | None:
        selected = self._create_file_dialog(
            webview.OPEN_DIALOG, ("PDF 文档 (*.pdf)",)
        )
        return self.inspect_pdf(selected[0]) if selected else None

    def inspect_pdf(self, source: str) -> dict:
        import fitz

        path = Path(source)
        with fitz.open(path) as document:
            pages = len(document)
            sample = "".join(document[i].get_text() for i in range(min(3, pages))).strip()
            return {"path": str(path), "name": path.name, "pages": pages,
                    "size": path.stat().st_size, "hasText": bool(sample),
                    "sample": re.sub(r"\s+", " ", sample)[:380], "preview": self._page_preview(path)}

    @staticmethod
    def _page_preview(path: Path) -> str:
        import base64
        import fitz

        with fitz.open(path) as document:
            image = document[0].get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False)
            return "data:image/jpeg;base64," + base64.b64encode(image.tobytes("jpeg")).decode()

    def start_translation(self, request: dict) -> dict:
        if self._model is None:
            raise RuntimeError("请先在「本地模型」中加载 GGUF 模型。")
        if self._workers:
            raise RuntimeError("已有翻译任务正在进行。")
        path = Path(request.get("path", ""))
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError("请重新选择 PDF 文档。")
        job_id = str(int(time.time() * 1000))
        job = {"id": job_id, "name": path.name, "status": "running", "progress": 0,
               "detail": "正在分析页面与排版", "outputs": [], "started": time.time()}
        self._jobs[job_id] = job
        self._settings.update({"language": request.get("language", "zh"),
                               "format": request.get("format", "dual"),
                               "threads": max(1, min(int(request.get("threads", 2)), 4)),
                               "ocr": bool(request.get("ocr", True)),
                               "translateTableText": bool(request.get("translateTableText", True))})
        self._save_settings()
        self._cancel.clear()
        self._workers = threading.Thread(target=self._translate_worker, args=(job_id, path, request), daemon=True)
        self._workers.start()
        return job

    def _translate_worker(self, job_id: str, path: Path, request: dict) -> None:
        try:
            from app.translator import translate_pdf

            last_emit_at = 0.0
            last_emit_progress = -1

            def progress(done: int, total: int, detail: str) -> None:
                nonlocal last_emit_at, last_emit_progress
                job = self._jobs[job_id]
                current_progress = round(done / max(total, 1) * 100)
                job["progress"] = current_progress
                job["detail"] = detail

                now = time.monotonic()
                if current_progress == last_emit_progress:
                    return
                if (current_progress < 100
                        and now - last_emit_at < 0.1):
                    return
                last_emit_at = now
                last_emit_progress = current_progress
                self._emit("job-updated", job)

            outputs = translate_pdf(path, self._model, Path(self._model_path).name, request,
                                    self._output_dir, progress, self._cancel)
            job = self._jobs[job_id]
            job.update({"status": "completed", "progress": 100, "detail": "翻译完成", "outputs": outputs})
            self._emit("job-updated", job)
            self._notify("文档翻译完成", f"已生成 {Path(outputs[0]['path']).name}", "success")
        except Exception as exc:
            if self._cancel.is_set():
                self._jobs[job_id].update({"status": "cancelled", "detail": "已取消"})
            else:
                self._jobs[job_id].update({"status": "failed", "detail": str(exc)})
                self._notify("翻译失败", str(exc), "error")
            self._emit("job-updated", self._jobs[job_id])
        finally:
            self._workers = None

    def cancel_translation(self) -> dict:
        self._cancel.set()
        return {"cancelled": True}

    def open_output(self, path: str = "") -> bool:
        target = Path(path) if path else self._output_dir
        if not path:
            target.mkdir(parents=True, exist_ok=True)
        if not target.resolve().is_relative_to(self._output_dir.resolve()) or not target.exists():
            raise ValueError("找不到输出文件。")
        os.startfile(target if target.is_dir() else target.parent)
        return True

    def choose_output_dir(self) -> dict:
        selected = self._create_file_dialog(webview.FOLDER_DIALOG)
        if selected:
            self._output_dir = Path(selected[0])
            self._settings["outputDir"] = str(self._output_dir)
            self._save_settings()
        return {"outputDir": str(self._output_dir)}

    def _emit(self, event: str, data: dict) -> None:
        if self._window:
            try:
                self._window.evaluate_js(f"window.studioEvent({json.dumps(event)}, {json.dumps(data, ensure_ascii=False)})")
            except Exception:
                pass

    def _notify(self, title: str, message: str, kind: str) -> None:
        self._emit("notification", {"title": title, "message": message, "kind": kind})

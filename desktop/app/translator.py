from __future__ import annotations

from pdf2zh.high_level import translate_stream
from pdf2zh.translator import BaseTranslator
import pdf2zh.converter as converter


def translate_pdf(source, model, model_name, request, output_dir, progress, cancel):
    import fitz
    from pdf2zh.doclayout import OnnxModel

    class GGUFTranslator(BaseTranslator):
        name = "local-gguf"

        def __init__(self, lang_in, lang_out, service_model, **kwargs):
            super().__init__(lang_in, lang_out, model_name, False)
            self.prompt_template = None

        def do_translate(self, text):
            if cancel.is_set():
                raise InterruptedError("翻译已取消")
            prompt = (
                "You are a professional academic translator. Translate the following text "
                f"from {self.lang_in} to {self.lang_out}. Preserve every mathematical formula, "
                "placeholder, number, citation and Markdown marker exactly. Output only the "
                "translated text, with no explanations or introductory phrases.\n\n" + text
            )
            response = model.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=float(request.get("temperature", 0.15)),
                max_tokens=min(2048, max(256, int(len(text) * 1.5))),
            )
            return response["choices"][0]["message"]["content"].strip()

    converter.LocalGGUFTranslator = GGUFTranslator

    output_dir.mkdir(parents=True, exist_ok=True)
    pages = _parse_pages(request.get("pages", "").strip())
    progress(0, 100, "正在准备字体与页面布局")
    mono, dual = translate_stream(
        source.read_bytes(), lang_in=request.get("sourceLanguage", "en"),
        lang_out=request.get("language", "zh"), service="local-gguf",
        thread=int(request.get("threads", 2)), pages=pages,
        ocr=bool(request.get("ocr", True)),
        ocr_engine="rapidocr",
        translate_table_text=bool(request.get("translateTableText", True)),
        callback=lambda bar: progress(bar.n, bar.total, "正在翻译并保留公式与排版"),
        cancellation_event=cancel,
        model=OnnxModel.from_pretrained(),
    )
    basename = source.stem
    mono_path = output_dir / f"{basename}-mono.pdf"
    dual_path = output_dir / f"{basename}-dual.pdf"
    mono_path.write_bytes(mono)
    dual_path.write_bytes(dual)
    if request.get("format", "dual") == "mono":
        dual_path.unlink(missing_ok=True)
        return [{"kind": "mono", "name": mono_path.name, "path": str(mono_path), "size": mono_path.stat().st_size}]
    if request.get("format", "dual") == "dual":
        mono_path.unlink(missing_ok=True)
        return [{"kind": "dual", "name": dual_path.name, "path": str(dual_path), "size": dual_path.stat().st_size}]
    side_path = output_dir / f"{basename}-side-by-side.pdf"
    _side_by_side(source, dual_path, side_path)
    outputs = [(key, path) for key, path in [("mono", mono_path), ("dual", dual_path), ("side", side_path)]
               if request.get("format", "dual") in ("all", key)]
    return [{"kind": key, "name": path.name, "path": str(path), "size": path.stat().st_size} for key, path in outputs]


def _parse_pages(value):
    if not value:
        return None
    pages = []
    for part in value.split(","):
        bits = part.split("-")
        if len(bits) == 2:
            pages.extend(range(int(bits[0]) - 1, int(bits[1])))
        else:
            pages.append(int(bits[0]) - 1)
    if any(page < 0 for page in pages):
        raise ValueError("页码必须从 1 开始。")
    return pages


def _side_by_side(source, dual_path, target):
    import fitz

    original = fitz.open(source)
    dual = fitz.open(dual_path)
    result = fitz.open()
    for number in range(len(original)):
        left = original[number]
        right = dual[number * 2 + 1]
        page = result.new_page(width=left.rect.width + right.rect.width, height=max(left.rect.height, right.rect.height))
        page.show_pdf_page(fitz.Rect(0, 0, left.rect.width, left.rect.height), original, number)
        page.show_pdf_page(fitz.Rect(left.rect.width, 0, left.rect.width + right.rect.width, right.rect.height), dual, number * 2 + 1)
    result.save(target, garbage=3, deflate=True)
    result.close()
    dual.close()
    original.close()

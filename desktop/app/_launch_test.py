from pathlib import Path

import hyperscan
from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater

from app.studio import Studio


def run():
    if not hyperscan.__version__ or PDFCreater is None:
        raise RuntimeError("Native translation dependencies did not load")
    app = Studio()
    info = app.inspect_pdf(str(Path("test-sample.pdf").resolve()))
    if info["pages"] != 1 or not info["hasText"]:
        raise RuntimeError("Packaged PDF import failed")
    if not info["preview"].startswith("data:image/jpeg"):
        raise RuntimeError("Packaged PDF preview failed")
    print("PACKAGED PDF IMPORT OK")


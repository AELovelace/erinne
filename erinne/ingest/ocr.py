"""OCR for scanned PDF pages via the `ocrmypdf` command-line tool.

On Fedora: `sudo dnf install ocrmypdf` (pulls in tesseract and ghostscript).
When the command isn't installed, ingestion continues and the file gets a
note listing the pages that have no text.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class OcrError(RuntimeError):
    pass


def available(command: str) -> bool:
    return shutil.which(command) is not None


def run(command: str, source: Path, dest: Path, timeout: int) -> None:
    """Write an OCR'd copy of `source` to `dest`, leaving pages that already
    have text untouched (--skip-text)."""
    args = [command, "--skip-text", "--output-type", "pdf", "--quiet", str(source), str(dest)]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"OCR timed out after {timeout} s.") from exc
    if result.returncode != 0 or not dest.exists():
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise OcrError(f"OCR failed (exit {result.returncode}): {detail[0]}")

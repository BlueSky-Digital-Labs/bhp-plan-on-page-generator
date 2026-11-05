"""Plain-text extraction for the four supported file types, with hard limits on everything."""

import io
import json
import re
import zipfile
from dataclasses import dataclass

from defusedxml import ElementTree
from pypdf import PdfReader

from app.appendix.model import clean_text
from app.attachments.markers import MAX_ATTACHMENT_CHARS

MAX_BYTES = 5 * 1024 * 1024  # the uploaded file
MAX_PDF_PAGES = 100
MAX_PART_BYTES = 10 * 1024 * 1024  # one XML part inside a Word or PowerPoint file, uncompressed
MAX_ZIP_ENTRIES = 5000
KINDS = ("pdf", "docx", "pptx", "json")

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


class AttachmentError(Exception):
    """unsupported_file_type, file_unreadable or file_has_no_text. Never carries file content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Extracted:
    name: str
    kind: str
    text: str
    truncated: bool


def safe_name(name: str) -> str:
    """A short, plain file name: no path, no control characters, nothing that breaks a marker."""
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r"[\x00-\x1f\x7f\[\]|]", "", base)
    base = " ".join(base.split())
    if len(base) > 80:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 80 - len(ext) - 1] + "." + ext) if dot else base[:80]
    return base or "attachment"


def _zip_part(archive: zipfile.ZipFile, name: str) -> bytes:
    with archive.open(name) as part:
        data = part.read(MAX_PART_BYTES + 1)
    if len(data) > MAX_PART_BYTES:
        raise AttachmentError("file_unreadable")
    return data


def _open_zip(data: bytes, required: str) -> zipfile.ZipFile:
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise AttachmentError("file_unreadable")
    archive = zipfile.ZipFile(io.BytesIO(data))
    if len(archive.infolist()) > MAX_ZIP_ENTRIES or required not in archive.namelist():
        raise AttachmentError("file_unreadable")  # not really a file of this kind
    return archive


def _docx(data: bytes) -> str:
    with _open_zip(data, "word/document.xml") as archive:
        root = ElementTree.fromstring(_zip_part(archive, "word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(f"{_W}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _pptx(data: bytes) -> str:
    with _open_zip(data, "ppt/presentation.xml") as archive:
        slides = sorted(
            (int(m.group(1)), name) for name in archive.namelist() if (m := _SLIDE.match(name))
        )
        out = []
        for number, name in slides:
            root = ElementTree.fromstring(_zip_part(archive, name))
            lines = [
                "".join(t.text or "" for t in paragraph.iter(f"{_A}t"))
                for paragraph in root.iter(f"{_A}p")
            ]
            out.append(f"Slide {number}:\n" + "\n".join(line for line in lines if line.strip()))
    return "\n\n".join(out)


def _pdf(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise AttachmentError("file_unreadable")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise AttachmentError("file_unreadable")  # password protected
    text: list[str] = []
    size = 0
    for page in reader.pages[:MAX_PDF_PAGES]:
        text.append(page.extract_text() or "")
        size += len(text[-1])
        if size > MAX_ATTACHMENT_CHARS * 2:  # more than will be used: stop reading
            break
    return "\n\n".join(text)


def _json(data: bytes) -> str:
    """The file pretty-printed, but never more than twice the text that will be used.

    Indenting makes the output grow with nesting depth, not with the file's size: a small, deeply
    nested file would otherwise print to hundreds of times its own size (and a file at the size
    limit to gigabytes) before anything could cut it. So the text is produced piece by piece and
    production stops as soon as there is more than will be used, like the PDF reader does.
    """
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2)
    text: list[str] = []
    size = 0
    for piece in encoder.iterencode(json.loads(data.decode("utf-8-sig"))):
        text.append(piece)
        size += len(piece)
        if size > MAX_ATTACHMENT_CHARS * 2:  # more than will be used: stop writing
            break
    return "".join(text)


_READERS = {"pdf": _pdf, "docx": _docx, "pptx": _pptx, "json": _json}


def extract(filename: str, data: bytes) -> Extracted:
    """The file's text, cleaned and cut to the limit, or an AttachmentError."""
    name = safe_name(filename)
    kind = name.rpartition(".")[2].lower() if "." in name else ""
    if kind not in KINDS:
        raise AttachmentError("unsupported_file_type")
    try:
        raw = _READERS[kind](data)
    except AttachmentError:
        raise
    except Exception:  # noqa: BLE001 - corrupt, hostile or unusual files are all just unreadable
        raise AttachmentError("file_unreadable") from None
    # Marker lookalikes are broken up before the length is measured, so the limit is exact.
    text = re.sub(r"\n{3,}", "\n\n", clean_text(raw)).replace("[[", "[ [").strip()
    if not text:
        raise AttachmentError("file_has_no_text")  # for example a scanned PDF
    truncated = len(text) > MAX_ATTACHMENT_CHARS
    if truncated:
        text = (
            text[:MAX_ATTACHMENT_CHARS].rsplit(None, 1)[0]
            if " " in text[:MAX_ATTACHMENT_CHARS]
            else text[:MAX_ATTACHMENT_CHARS]
        )
    return Extracted(name, kind, text, truncated)

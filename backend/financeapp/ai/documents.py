"""Turning an uploaded file into what can be read: text (from a spreadsheet or a PDF) or pictures (a screenshot, a
photo, or the pages of a scanned PDF).

Every file is opened defensively: the type comes from its first bytes, not its name; spreadsheets are checked for
zip bombs before opening (and their XML parsed with defusedxml); pictures are checked for size before decoding,
then re-encoded, which drops location and camera details (EXIF) and anything hidden after the image data.
"""

import csv
import io
import zipfile
from dataclasses import dataclass, field

import openpyxl.xml
from openpyxl import load_workbook
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from financeapp.ai.providers import Picture

# openpyxl parses with defusedxml when it's installed (no entity expansion, no external entities). Make sure it is.
if not openpyxl.xml.DEFUSEDXML:
    raise ImportError("defusedxml must be installed")

MAX_PAGES = 4  # pages of a scan sent as pictures (each costs about as much as a page of text)
MAX_TEXT = 60_000  # characters of text sent (about 20,000 tokens)
MAX_SHEET_ROWS = 5_000
MAX_UNZIPPED = 50 * 1024 * 1024
MAX_PIXELS = 40_000_000
LONG_SIDE = 2000  # pictures are scaled down to this: enough to read small print, cheap to send
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class DocumentError(ValueError):
    """The file couldn't be opened. The message is shown to the person as-is."""


@dataclass
class Document:
    kind: str  # spreadsheet | pdf | scan | picture | text
    text: str = ""
    pictures: list[Picture] = field(default_factory=list)
    sheets: list[str] = field(default_factory=list)  # each sheet of a spreadsheet as CSV
    pages: int = 0
    truncated: bool = False


def sniff(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1", b"ftyphevc"):
        return "heic"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    return "text"


def picture_from(img: Image.Image) -> Picture:
    """A picture scaled down and re-encoded as JPEG (or PNG for screenshots with few colours), without metadata."""
    img.load()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > LONG_SIDE:
        img.thumbnail((LONG_SIDE, LONG_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    return Picture("image/jpeg", out.getvalue())


def open_picture(data: bytes) -> Picture:
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise DocumentError("That picture is too big. Take a screenshot instead.")
            return picture_from(img)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise DocumentError("That picture couldn't be opened. Try a PNG or JPEG.") from None


def open_spreadsheet(data: bytes) -> Document:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except zipfile.BadZipFile:
        raise DocumentError("That file couldn't be opened.") from None
    names = {i.filename for i in infos}
    if "xl/workbook.xml" not in names:
        raise DocumentError("That's a zip file, not a spreadsheet. Upload an .xlsx, .csv, PDF or picture.")
    if len(infos) > 2000 or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise DocumentError("That spreadsheet is too big to open. Save a smaller one, or export it as CSV.")
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise DocumentError("That spreadsheet couldn't be opened. Try saving it again, or export it as CSV.") from None
    sheets = []
    try:
        for ws in wb.worksheets[:10]:
            buf = io.StringIO()
            w = csv.writer(buf)
            rows = 0
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v)) for v in row]
                while cells and cells[-1] == "":
                    cells.pop()
                if not cells:
                    continue
                w.writerow(cells)
                rows += 1
                if rows >= MAX_SHEET_ROWS:
                    break
            if rows:
                sheets.append(buf.getvalue())
    finally:
        wb.close()
    if not sheets:
        raise DocumentError("That spreadsheet is empty.")
    text = "\n\n".join(f"--- Sheet {i + 1} ---\n{s}" for i, s in enumerate(sheets))
    return Document("spreadsheet", text=text[:MAX_TEXT], sheets=sheets, truncated=len(text) > MAX_TEXT)


def open_pdf(data: bytes) -> Document:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError("That PDF is password-protected. Save a copy without the password and upload that.")
        pages = reader.pages
        count = len(pages)
        text = "\n\n".join((p.extract_text(extraction_mode="layout") or "") for p in pages[:30])
    except DocumentError:
        raise
    except Exception:
        raise DocumentError("That PDF couldn't be opened. Try downloading it again.") from None
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) >= 40:
        return Document("pdf", text=text[:MAX_TEXT], pages=count, truncated=len(text) > MAX_TEXT)
    # A scan: each page is (usually) one big picture. Take the largest picture on each of the first pages.
    pictures = []
    try:
        for page in pages[:MAX_PAGES]:
            best = None
            for image in page.images:
                img = image.image
                if img is not None and (best is None or img.width * img.height > best.width * best.height):
                    best = img
            if best is not None:
                if best.width * best.height > MAX_PIXELS:
                    raise DocumentError("That scan is too big. Scan it at a lower resolution, or take a screenshot.")
                pictures.append(picture_from(best))
    except DocumentError:
        raise
    except Exception:
        raise DocumentError("The pages of that scan couldn't be read. Try a screenshot of it instead.") from None
    if not pictures:
        raise DocumentError("That PDF has no text or pictures in it to read.")
    return Document("scan", pictures=pictures, pages=count, truncated=count > MAX_PAGES)


def open_document(data: bytes) -> Document:
    kind = sniff(data)
    if kind == "pdf":
        return open_pdf(data)
    if kind in ("png", "jpeg", "webp"):
        return Document("picture", pictures=[open_picture(data)], pages=1)
    if kind == "heic":
        raise DocumentError("HEIC photos can't be read here. Take a screenshot of it, or share it as JPEG.")
    if kind == "zip":
        return open_spreadsheet(data)
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DocumentError("That file type can't be read. Upload a CSV, spreadsheet, PDF or picture.")
    if "\x00" in text[:4096]:
        raise DocumentError("That file type can't be read. Upload a CSV, spreadsheet, PDF or picture.")
    return Document("text", text=text[:MAX_TEXT], truncated=len(text) > MAX_TEXT)

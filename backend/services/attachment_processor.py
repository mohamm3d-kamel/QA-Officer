"""Attachment Processor - Safe ingestion, parsing, and vision preparation for chat images and files."""
import base64
import csv
import io
import json
import logging
import mimetypes
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

import config

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB per file
MAX_VISION_BYTES = 4 * 1024 * 1024   # Downscale/compress image payload if > 4 MB
MAX_PDF_BINARY_BYTES = 12 * 1024 * 1024
MAX_EXTRACTED_CHARS = 12000
MAX_DB_EXTRACTED_CHARS = 4000
MAX_IMAGE_DIM = 2048

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".ico"}
VISION_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_EXTS = {
    ".txt", ".log", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".har",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".properties",
    ".html", ".htm", ".css", ".scss", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".py", ".java", ".kt", ".cs", ".go", ".rs", ".rb", ".php", ".sql", ".sh", ".ps1",
    ".bat", ".cmd", ".diff", ".patch", ".svg",
}

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_ERROR_LINE_RE = re.compile(
    r"\b(error|exception|traceback|fatal|critical|panic|failed|failure|uncaught|unhandled|timeout|refused|500|502|503|504|404|403|401|422|400)\b",
    re.IGNORECASE,
)


def sanitize_filename(filename: str) -> str:
    base = Path((filename or "attachment").replace("\\", "/")).name.strip()
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip("._")
    return cleaned[:120] or "attachment"


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def detect_mime_type(filename: str, raw_bytes: bytes, declared_mime: Optional[str] = None) -> str:
    ext = Path(filename).suffix.lower()
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
        return "image/webp"
    if raw_bytes.startswith(b"BM"):
        return "image/bmp"
    if raw_bytes.startswith(b"%PDF-"):
        return "application/pdf"

    if ext == ".har":
        return "application/json"
    if ext == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext == ".pptx":
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if ext in (".log", ".md", ".diff", ".patch", ".sql", ".ts", ".tsx", ".jsx", ".yaml", ".yml"):
        return "text/plain"

    guessed, _ = mimetypes.guess_type(filename)
    if declared_mime and declared_mime not in ("", "application/octet-stream"):
        return declared_mime.split(";")[0].strip().lower()
    if guessed:
        return guessed
    return "application/octet-stream"


def resolve_stored_upload(stored_name: str) -> Optional[Path]:
    """Safely resolve a filename inside CHAT_UPLOADS_DIR without path traversal."""
    safe_name = Path((stored_name or "").replace("\\", "/")).name
    if not safe_name or safe_name in (".", ".."):
        return None
    base_dir = config.CHAT_UPLOADS_DIR.resolve()
    target = (base_dir / safe_name).resolve()
    if target.parent != base_dir or not target.exists() or not target.is_file():
        return None
    return target


def _encode_image_for_vision(raw_bytes: bytes, mime_type: str) -> Tuple[str, str]:
    """Return (llm_mime_type, base64_data), resizing/re-encoding huge or exotic images safely."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            needs_convert = mime_type not in VISION_MIMES
            needs_resize = max(img.width, img.height) > MAX_IMAGE_DIM or len(raw_bytes) > MAX_VISION_BYTES
            if not needs_convert and not needs_resize:
                return mime_type, base64.b64encode(raw_bytes).decode("ascii")

            worked = img
            if max(worked.width, worked.height) > MAX_IMAGE_DIM:
                ratio = MAX_IMAGE_DIM / float(max(worked.width, worked.height))
                new_size = (max(1, int(worked.width * ratio)), max(1, int(worked.height * ratio)))
                worked = worked.resize(new_size, Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            if mime_type == "image/jpeg" or (len(raw_bytes) > MAX_VISION_BYTES and worked.mode in ("RGB", "L")):
                if worked.mode != "RGB":
                    worked = worked.convert("RGB")
                worked.save(buf, format="JPEG", quality=86, optimize=True)
                return "image/jpeg", base64.b64encode(buf.getvalue()).decode("ascii")

            if worked.mode not in ("RGB", "RGBA", "L", "LA"):
                worked = worked.convert("RGBA")
            worked.save(buf, format="PNG", optimize=True)
            return "image/png", base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        fallback_mime = mime_type if mime_type in VISION_MIMES else "image/png"
        return fallback_mime, base64.b64encode(raw_bytes).decode("ascii")


def _extract_error_highlights(text: str, max_items: int = 8) -> List[str]:
    highlights: List[str] = []
    seen = set()
    for idx, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if _ERROR_LINE_RE.search(stripped):
            snippet = f"L{idx}: {stripped[:180]}"
            if snippet not in seen:
                seen.add(snippet)
                highlights.append(snippet)
                if len(highlights) >= max_items:
                    break
    return highlights


def _extract_pdf_text(raw_bytes: bytes) -> Tuple[str, int]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw_bytes))
        page_count = len(reader.pages)
        chunks: List[str] = []
        total_len = 0
        for idx, page in enumerate(reader.pages, 1):
            try:
                ptxt = (page.extract_text() or "").strip()
            except Exception:
                ptxt = ""
            if ptxt:
                block = f"--- Page {idx} ---\n{ptxt}"
                chunks.append(block)
                total_len += len(block)
                if total_len >= MAX_EXTRACTED_CHARS:
                    break
        return "\n\n".join(chunks)[:MAX_EXTRACTED_CHARS], page_count
    except Exception as exc:
        logger.warning(f"PDF text extraction error: {exc}")
        return "", 0


def _extract_docx_text(raw_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            if "word/document.xml" not in zf.namelist():
                return ""
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        paragraphs: List[str] = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "p":
                texts = [t.text for t in elem.iter() if t.tag.split("}")[-1] == "t" and t.text]
                line = "".join(texts).strip()
                if line:
                    paragraphs.append(line)
        return "\n".join(paragraphs)[:MAX_EXTRACTED_CHARS]
    except Exception as exc:
        logger.warning(f"DOCX extraction error: {exc}")
        return ""


def _extract_xlsx_text(raw_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            names = set(zf.namelist())
            shared: List[str] = []
            if "xl/sharedStrings.xml" in names:
                sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in sroot:
                    if si.tag.split("}")[-1] == "si":
                        txt = "".join(t.text or "" for t in si.iter() if t.tag.split("}")[-1] == "t")
                        shared.append(txt)

            sheet_names: List[str] = []
            if "xl/workbook.xml" in names:
                wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
                for sh in wb_root.iter():
                    if sh.tag.split("}")[-1] == "sheet" and sh.attrib.get("name"):
                        sheet_names.append(sh.attrib["name"])

            sheet_files = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
            out_blocks: List[str] = []
            for idx, sfile in enumerate(sheet_files[:6]):
                title = sheet_names[idx] if idx < len(sheet_names) else Path(sfile).name
                ws_root = ET.fromstring(zf.read(sfile))
                rows_text: List[str] = []
                for row_el in ws_root.iter():
                    if row_el.tag.split("}")[-1] != "row":
                        continue
                    vals: List[str] = []
                    for c_el in row_el:
                        if c_el.tag.split("}")[-1] != "c":
                            continue
                        ctype = c_el.attrib.get("t", "")
                        val = ""
                        for child in c_el:
                            ctag = child.tag.split("}")[-1]
                            if ctag == "v" and child.text is not None:
                                if ctype == "s" and child.text.isdigit() and int(child.text) < len(shared):
                                    val = shared[int(child.text)]
                                else:
                                    val = child.text
                            elif ctag == "is":
                                val = "".join(t.text or "" for t in child.iter() if t.tag.split("}")[-1] == "t")
                        vals.append(val)
                    if any(v.strip() for v in vals):
                        rows_text.append(" | ".join(vals))
                        if len(rows_text) >= 120:
                            break
                if rows_text:
                    out_blocks.append(f"[Sheet: {title}]\n" + "\n".join(rows_text))
            return "\n\n".join(out_blocks)[:MAX_EXTRACTED_CHARS]
    except Exception as exc:
        logger.warning(f"XLSX extraction error: {exc}")
        return ""


def _extract_pptx_text(raw_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            slide_files = sorted(
                (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                key=lambda s: int(re.search(r"(\d+)", Path(s).stem).group(1)),
            )
            blocks: List[str] = []
            for idx, sf in enumerate(slide_files[:25], 1):
                root = ET.fromstring(zf.read(sf))
                texts = [t.text.strip() for t in root.iter() if t.tag.split("}")[-1] == "t" and t.text and t.text.strip()]
                if texts:
                    blocks.append(f"--- Slide {idx} ---\n" + "\n".join(texts))
            return "\n\n".join(blocks)[:MAX_EXTRACTED_CHARS]
    except Exception as exc:
        logger.warning(f"PPTX extraction error: {exc}")
        return ""


def _extract_har_text(raw_text: str) -> Tuple[str, str, List[str]]:
    try:
        data = json.loads(raw_text)
        entries = (data.get("log") or {}).get("entries") or []
        if not isinstance(entries, list):
            return "", "", []
        failures: List[str] = []
        highlights: List[str] = []
        lines: List[str] = [f"HAR Capture: {len(entries)} network request(s)"]
        for idx, ent in enumerate(entries, 1):
            req = ent.get("request") or {}
            resp = ent.get("response") or {}
            method = req.get("method", "GET")
            url = req.get("url", "")
            status = resp.get("status", 0)
            status_txt = resp.get("statusText", "")
            time_ms = round(float(ent.get("time") or 0), 1)
            row = f"#{idx} {method} {url} -> {status} {status_txt} ({time_ms}ms)"
            if status == 0 or (isinstance(status, int) and status >= 400):
                err_body = (((resp.get("content") or {}).get("text")) or "").strip().replace("\n", " ")[:220]
                fail_line = f"{row}" + (f" | body={err_body}" if err_body else "")
                failures.append(fail_line)
                highlights.append(fail_line[:180])
            elif idx <= 15:
                lines.append(row)
        if failures:
            lines.append("\nFailed / Error Requests:")
            lines.extend(failures[:30])
        summary = f"HAR network trace ({len(entries)} requests, {len(failures)} failing >=400/error)"
        return "\n".join(lines)[:MAX_EXTRACTED_CHARS], summary, highlights[:8]
    except Exception:
        return "", "", []


def _extract_structured_text(ext: str, raw_bytes: bytes) -> Dict[str, Any]:
    """Decode & summarize text/code/log/csv/json/har/zip/pdf/office files."""
    if ext == ".pdf":
        text, pages = _extract_pdf_text(raw_bytes)
        highlights = _extract_error_highlights(text) if text else []
        note = "" if text else "No selectable text layer detected (scanned/image PDF — sent natively to multimodal AI)."
        summary = f"PDF document ({pages or '?'} page(s), {format_size(len(raw_bytes))})" + (f" — {note}" if note else "")
        return {
            "extracted_text": text or note,
            "summary": summary,
            "highlights": highlights,
            "page_count": pages,
            "line_count": len(text.splitlines()) if text else 0,
            "is_pdf": True,
        }

    if ext == ".docx":
        text = _extract_docx_text(raw_bytes)
        return {
            "extracted_text": text,
            "summary": f"Word document ({len(text.splitlines())} lines extracted, {format_size(len(raw_bytes))})",
            "highlights": _extract_error_highlights(text),
            "line_count": len(text.splitlines()),
        }

    if ext == ".xlsx":
        text = _extract_xlsx_text(raw_bytes)
        return {
            "extracted_text": text,
            "summary": f"Excel workbook ({len(text.splitlines())} rows/lines extracted, {format_size(len(raw_bytes))})",
            "highlights": _extract_error_highlights(text),
            "line_count": len(text.splitlines()),
        }

    if ext == ".pptx":
        text = _extract_pptx_text(raw_bytes)
        return {
            "extracted_text": text,
            "summary": f"PowerPoint deck ({len(text.splitlines())} lines extracted, {format_size(len(raw_bytes))})",
            "highlights": _extract_error_highlights(text),
            "line_count": len(text.splitlines()),
        }

    if ext == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()][:50]
                lines = [f"ZIP Archive ({len(infos)} entries shown):"]
                uncompressed = 0
                for inf in infos:
                    lines.append(f"- {inf.filename} ({format_size(inf.file_size)})")
                for inf in infos:
                    if Path(inf.filename).suffix.lower() in (".txt", ".log", ".json", ".md", ".csv") and inf.file_size <= 64 * 1024:
                        if uncompressed + inf.file_size > 512 * 1024:
                            break
                        uncompressed += inf.file_size
                        snippet = zf.read(inf.filename).decode("utf-8", "replace")[:1800]
                        lines.append(f"\n--- {inf.filename} ---\n{snippet}")
                text = "\n".join(lines)[:MAX_EXTRACTED_CHARS]
                return {
                    "extracted_text": text,
                    "summary": f"ZIP archive ({len(infos)} files, {format_size(len(raw_bytes))})",
                    "highlights": _extract_error_highlights(text),
                    "line_count": len(text.splitlines()),
                }
        except Exception:
            return {"extracted_text": "", "summary": f"ZIP archive ({format_size(len(raw_bytes))})", "highlights": [], "line_count": 0}

    # Check if mostly readable text
    sample = raw_bytes[:4096]
    if b"\x00" in sample and ext not in TEXT_EXTS:
        return {
            "extracted_text": "",
            "summary": f"Binary file ({ext or 'unknown'}, {format_size(len(raw_bytes))})",
            "highlights": [],
            "line_count": 0,
        }

    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        decoded = raw_bytes.decode("latin-1", "replace")

    line_count = len(decoded.splitlines())
    if ext == ".har":
        har_text, har_sum, har_hl = _extract_har_text(decoded)
        if har_text:
            return {
                "extracted_text": har_text,
                "summary": har_sum,
                "highlights": har_hl,
                "line_count": line_count,
            }

    if ext == ".json":
        try:
            parsed = json.loads(decoded)
            if isinstance(parsed, dict):
                desc = f"JSON object with keys: {', '.join(list(parsed.keys())[:10])}"
            elif isinstance(parsed, list):
                desc = f"JSON array ({len(parsed)} item(s))"
            else:
                desc = f"JSON ({type(parsed).__name__})"
            pretty = json.dumps(parsed, indent=2, ensure_ascii=False)[:MAX_EXTRACTED_CHARS]
            return {
                "extracted_text": pretty,
                "summary": f"{desc}, {format_size(len(raw_bytes))}",
                "highlights": _extract_error_highlights(pretty),
                "line_count": line_count,
            }
        except Exception:
            pass

    if ext in (".csv", ".tsv"):
        delim = "\t" if ext == ".tsv" else ","
        try:
            reader = list(csv.reader(io.StringIO(decoded), delimiter=delim))
            header = reader[0] if reader else []
            preview_lines = [" | ".join(r) for r in reader[:80]]
            text = "\n".join(preview_lines)[:MAX_EXTRACTED_CHARS]
            return {
                "extracted_text": text,
                "summary": f"{ext[1:].upper()} table ({len(reader)} row(s), columns: {', '.join(header[:8])})",
                "highlights": _extract_error_highlights(text),
                "line_count": len(reader),
            }
        except Exception:
            pass

    return {
        "extracted_text": decoded[:MAX_EXTRACTED_CHARS],
        "summary": f"Text/file ({line_count} line(s), {format_size(len(raw_bytes))})",
        "highlights": _extract_error_highlights(decoded[:MAX_EXTRACTED_CHARS]),
        "line_count": line_count,
    }


def process_uploaded_bytes(
    filename: str,
    raw_bytes: bytes,
    declared_mime: Optional[str] = None,
    save_to_disk: bool = True,
) -> Dict[str, Any]:
    """Validate, inspect, extract text/image info, and persist a chat attachment."""
    safe_name = sanitize_filename(filename)
    ext = Path(safe_name).suffix.lower()
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File '{safe_name}' exceeds the {format_size(MAX_UPLOAD_BYTES)} size limit.")

    mime_type = detect_mime_type(safe_name, raw_bytes, declared_mime)
    is_image = mime_type.startswith("image/") or ext in IMAGE_EXTS
    is_pdf = mime_type == "application/pdf" or ext == ".pdf"

    stored_name = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    if save_to_disk:
        config.CHAT_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        dest = config.CHAT_UPLOADS_DIR / stored_name
        dest.write_bytes(raw_bytes)

    width = None
    height = None
    summary = ""
    extracted_text = ""
    highlights: List[str] = []
    page_count = None
    line_count = None

    if is_image:
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                width, height = img.size
                img_fmt = img.format or ext.lstrip(".").upper() or "IMAGE"
                mode = img.mode
                frames = getattr(img, "n_frames", 1)
                frame_note = f", {frames} frames" if frames and frames > 1 else ""
                summary = f"{img_fmt} image ({width}x{height} px, {mode}{frame_note}, {format_size(len(raw_bytes))})"
        except Exception as img_err:
            logger.warning(f"Image metadata inspection failed for {safe_name}: {img_err}")
            is_image = False
            summary = f"Unreadable image file ({format_size(len(raw_bytes))}): {img_err}"
    else:
        parsed = _extract_structured_text(ext, raw_bytes)
        extracted_text = parsed.get("extracted_text") or ""
        summary = parsed.get("summary") or f"File ({format_size(len(raw_bytes))})"
        highlights = parsed.get("highlights") or []
        page_count = parsed.get("page_count")
        line_count = parsed.get("line_count")
        if parsed.get("is_pdf"):
            is_pdf = True

    meta: Dict[str, Any] = {
        "id": stored_name,
        "stored_name": stored_name,
        "name": safe_name,
        "mime_type": mime_type,
        "size_bytes": len(raw_bytes),
        "is_image": is_image,
        "is_pdf": is_pdf,
        "width": width,
        "height": height,
        "page_count": page_count,
        "line_count": line_count,
        "url": f"/api/chat/uploads/{stored_name}" if save_to_disk else None,
        "summary": summary,
        "highlights": highlights[:8],
        "extracted_text": extracted_text[:MAX_DB_EXTRACTED_CHARS] if extracted_text else "",
        "_full_extracted_text": extracted_text[:MAX_EXTRACTED_CHARS] if extracted_text else "",
    }
    return meta


def sanitize_attachment_for_db(att: Dict[str, Any]) -> Dict[str, Any]:
    """Return lightweight JSON-safe attachment metadata for DB persistence and UI rendering."""
    keep_keys = (
        "id", "stored_name", "name", "mime_type", "size_bytes", "is_image", "is_pdf",
        "width", "height", "page_count", "line_count", "url", "summary", "highlights",
    )
    out = {k: att[k] for k in keep_keys if att.get(k) is not None}
    txt = att.get("extracted_text") or att.get("content") or ""
    if txt:
        out["extracted_text"] = str(txt)[:MAX_DB_EXTRACTED_CHARS]
    return out


def _decode_inline_base64(raw_val: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Decode base64 string or data:<mime>;base64,<payload> URI."""
    val = (raw_val or "").strip()
    if not val:
        return None, None
    mime_hint = None
    if val.startswith("data:") and ";base64," in val[:80]:
        header, val = val.split(";base64,", 1)
        mime_hint = header[5:].strip().lower() or None
    try:
        return base64.b64decode(val, validate=True), mime_hint
    except Exception:
        try:
            return base64.b64decode(val), mime_hint
        except Exception:
            return None, mime_hint


def hydrate_attachments_for_request(
    raw_attachments: Optional[List[Dict[str, Any]]],
    include_binary_for_vision: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Hydrate incoming chat attachments for LLM execution and return sanitized DB metadata."""
    if not raw_attachments:
        return [], []

    llm_items: List[Dict[str, Any]] = []
    db_items: List[Dict[str, Any]] = []

    for att in raw_attachments[:8]:
        if not isinstance(att, dict):
            continue
        stored_name = att.get("stored_name") or ""
        stored_path = resolve_stored_upload(stored_name) if stored_name else None

        if stored_path is not None:
            raw_bytes = stored_path.read_bytes()
            base_meta = dict(att)
            if not base_meta.get("summary") or (not base_meta.get("extracted_text") and not base_meta.get("is_image")):
                reprocessed = process_uploaded_bytes(
                    filename=att.get("name") or stored_path.name,
                    raw_bytes=raw_bytes,
                    declared_mime=att.get("mime_type"),
                    save_to_disk=False,
                )
                for k, v in reprocessed.items():
                    if k not in ("stored_name", "id", "url") and base_meta.get(k) is None:
                        base_meta[k] = v
                if not base_meta.get("extracted_text") and reprocessed.get("_full_extracted_text"):
                    base_meta["extracted_text"] = reprocessed["_full_extracted_text"]
            full_text = base_meta.get("_full_extracted_text") or base_meta.get("extracted_text") or ""
            llm_att = sanitize_attachment_for_db(base_meta)
            llm_att["extracted_text"] = full_text[:MAX_EXTRACTED_CHARS]
            llm_att["file_path"] = str(stored_path)

            if include_binary_for_vision:
                if llm_att.get("is_image"):
                    llm_mime, b64 = _encode_image_for_vision(raw_bytes, llm_att.get("mime_type") or "image/png")
                    llm_att["llm_mime_type"] = llm_mime
                    llm_att["data_base64"] = b64
                elif llm_att.get("is_pdf") and len(raw_bytes) <= MAX_PDF_BINARY_BYTES:
                    llm_att["llm_mime_type"] = "application/pdf"
                    llm_att["data_base64"] = base64.b64encode(raw_bytes).decode("ascii")

            llm_items.append(llm_att)
            db_items.append(sanitize_attachment_for_db(base_meta))
            continue

        # Handle inline base64 (direct API callers or pasted data URI)
        raw_b64 = att.get("data_base64") or ""
        data_uri_or_b64 = raw_b64 or (att.get("content") if isinstance(att.get("content"), str) and att.get("content", "").startswith("data:") else "")
        if data_uri_or_b64:
            decoded_bytes, uri_mime = _decode_inline_base64(data_uri_or_b64)
            if decoded_bytes is not None:
                proc = process_uploaded_bytes(
                    filename=att.get("name") or "attachment",
                    raw_bytes=decoded_bytes,
                    declared_mime=att.get("mime_type") or uri_mime,
                    save_to_disk=True,
                )
                llm_att = sanitize_attachment_for_db(proc)
                llm_att["extracted_text"] = proc.get("_full_extracted_text") or ""
                if proc.get("stored_name"):
                    p = resolve_stored_upload(proc["stored_name"])
                    if p:
                        llm_att["file_path"] = str(p)
                if include_binary_for_vision:
                    if proc.get("is_image"):
                        llm_mime, b64 = _encode_image_for_vision(decoded_bytes, proc.get("mime_type") or "image/png")
                        llm_att["llm_mime_type"] = llm_mime
                        llm_att["data_base64"] = b64
                    elif proc.get("is_pdf") and len(decoded_bytes) <= MAX_PDF_BINARY_BYTES:
                        llm_att["llm_mime_type"] = "application/pdf"
                        llm_att["data_base64"] = base64.b64encode(decoded_bytes).decode("ascii")
                llm_items.append(llm_att)
                db_items.append(sanitize_attachment_for_db(proc))
                continue

        # Handle plain text inline attachment
        inline_text = str(att.get("extracted_text") or att.get("content") or "")
        safe_name = sanitize_filename(att.get("name") or "attachment.txt")
        proc = process_uploaded_bytes(
            filename=safe_name,
            raw_bytes=inline_text.encode("utf-8"),
            declared_mime=att.get("mime_type") or "text/plain",
            save_to_disk=bool(inline_text.strip()),
        )
        llm_att = sanitize_attachment_for_db(proc)
        llm_att["extracted_text"] = proc.get("_full_extracted_text") or inline_text[:MAX_EXTRACTED_CHARS]
        if proc.get("stored_name"):
            p = resolve_stored_upload(proc["stored_name"])
            if p:
                llm_att["file_path"] = str(p)
        llm_items.append(llm_att)
        db_items.append(sanitize_attachment_for_db(proc))

    return llm_items, db_items


def hydrate_history_attachments(
    history_item: Dict[str, Any],
    include_binary: bool = False,
) -> List[Dict[str, Any]]:
    """Load attachment summaries (and optionally vision bytes) from a stored history entry."""
    raw = history_item.get("attachments") or []
    if not isinstance(raw, list) or not raw:
        return []
    hydrated, _ = hydrate_attachments_for_request(raw, include_binary_for_vision=include_binary)
    return hydrated


def format_attachment_text_block(att: Dict[str, Any], max_chars: int = 3500) -> str:
    """Format a readable text description of an attachment for prompts or bug descriptions."""
    name = att.get("name") or "attachment"
    summary = att.get("summary") or att.get("mime_type") or ""
    header = f"[Attachment: {name}" + (f" — {summary}" if summary else "") + "]"
    parts = [header]
    highlights = att.get("highlights") or []
    if highlights:
        parts.append("Key Highlights:\n" + "\n".join(f"- {h}" for h in highlights[:6]))
    body = (att.get("extracted_text") or att.get("content") or "").strip()
    if body:
        parts.append(body[:max_chars])
    return "\n".join(parts)

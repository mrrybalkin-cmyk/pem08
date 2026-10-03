"""Local, bounded PDF preparation. No persistence, HTTP or provider operations."""

import base64
from dataclasses import dataclass

import pymupdf


class InvalidPDF(ValueError):
    pass


class PDFTooLarge(InvalidPDF):
    pass


def image_data_url(content: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


def select_pages(page_count: int, limit: int) -> list[int]:
    """Ascending public (1-based) evenly spaced pages, including both ends."""
    if page_count < 1 or limit < 1:
        raise InvalidPDF("PDF has no usable pages or invalid page limit")
    count = min(page_count, limit)
    if count == 1:
        return [1]
    return [1 + i * (page_count - 1) // (count - 1) for i in range(count)]


@dataclass(frozen=True)
class PreparedPDF:
    extracted_text: str
    image_inputs: list[str]
    metadata: dict


def prepare_pdf(content: bytes, declared_mime: str | None, max_bytes: int,
                page_limit: int, text_limit: int, *, selected_pages: list[int] | None = None) -> PreparedPDF:
    if declared_mime != "application/pdf" or not content.startswith(b"%PDF"):
        raise InvalidPDF("Declared MIME and PDF signature must agree")
    if len(content) > max_bytes:
        raise PDFTooLarge("PDF exceeds MAX_PDF_MB")
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if not document.is_pdf or document.is_repaired:
                raise InvalidPDF("Damaged PDF")
            # Owner-password-only PDFs may already be open while still encrypted.
            if (document.needs_pass or document.is_encrypted
                    or (document.metadata or {}).get("encryption")):
                raise InvalidPDF("Encrypted PDF is not supported")
            count = document.page_count
            pages = select_pages(count, page_limit) if selected_pages is None else selected_pages
            if (not pages or len(pages) > page_limit or any(type(page) is not int or not 1 <= page <= count for page in pages)
                    or pages != sorted(set(pages)) or pages[0] != 1):
                raise InvalidPDF("Invalid persisted page selection")
            chunks, images = [], []
            text_pages = 0
            remaining = text_limit
            truncated = False
            for number in pages:
                page = document[number - 1]
                text = page.get_text("text").strip()
                text_pages += bool(text)
                chunk = f"[Page {number}]\n{text}\n"
                chunks.append(chunk[:remaining])
                truncated |= len(chunk) > remaining
                remaining = max(0, remaining - len(chunk))
                longest = max(page.rect.width, page.rect.height)
                if longest <= 0:
                    raise InvalidPDF("PDF has unusable page dimensions")
                scale = min(96 / 72, 1600 / longest)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
                images.append(image_data_url(pixmap.tobytes("png"), "image/png"))
            return PreparedPDF("".join(chunks), images, {
                "page_count": count, "selected_pages": pages, "selected_page_count": len(pages),
                "partial_analysis": len(pages) < count, "text_page_count": text_pages,
                "visual_page_count": len(images), "scanned_page_count": len(pages) - text_pages,
                "text_truncated": truncated, "text_char_limit": text_limit,
                "render_dpi": 96, "render_max_dimension": 1600, "size_bytes": len(content),
                "analyzed_page_limit": page_limit,
            })
    except InvalidPDF:
        raise
    except (RuntimeError, ValueError) as exc:
        raise InvalidPDF("Unreadable PDF") from exc


def add_pdf_limitations(result, metadata: dict):
    """Preserve validated findings; reserve space for truthful coverage limitations."""
    additions = []
    if metadata.get("partial_analysis"):
        additions.append(f"Анализ выполнен по выбранным {metadata['selected_page_count']} из {metadata['page_count']} страниц PDF.")
    if metadata.get("text_truncated"):
        additions.append("Извлечённый текст PDF ограничен бюджетом контекста; изображения выбранных страниц приложены.")
    if not additions:
        return result
    data = result.model_dump(mode="json")
    existing = [item for item in data["limitations"] if item not in additions]
    data["limitations"] = existing[:8 - len(additions)] + additions
    return type(result).model_validate(data)

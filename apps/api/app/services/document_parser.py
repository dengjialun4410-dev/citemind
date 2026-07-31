import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader
import wordninja


@dataclass
class PageText:
    page_number: int
    text: str


@dataclass
class ParsedChunk:
    content: str
    page_number: int
    section_path: str
    chunk_index: int


def extract_pages(content: bytes, extension: str) -> list[PageText]:
    extension = extension.lower()
    if extension == ".pdf":
        reader = PdfReader(BytesIO(content))
        return [PageText(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]
    if extension == ".docx":
        document = DocxDocument(BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return [PageText(1, text)]
    if extension in {".txt", ".md"}:
        return [PageText(1, content.decode("utf-8", errors="replace"))]
    raise ValueError(f"暂不支持 {extension or '未知'} 格式")


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)
    text = re.sub(r"(?<=\))(?=\d)", " ", text)
    text = re.sub(
        r"\b[a-z]{18,}\b",
        lambda match: " ".join(wordninja.split(match.group(0))),
        text,
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_units(text: str, chunk_size: int) -> list[str]:
    units = [part.strip() for part in re.split(r"\n+|(?<=[。！？.!?])\s+", text) if part.strip()]
    result: list[str] = []
    for unit in units:
        while len(unit) > chunk_size:
            boundary = unit.rfind(" ", 0, chunk_size)
            boundary = boundary if boundary >= chunk_size // 2 else chunk_size
            result.append(unit[:boundary].strip())
            unit = unit[boundary:].strip()
        if unit:
            result.append(unit)
    return result


def _overlap_tail(parts: list[str], overlap: int) -> list[str]:
    if not overlap:
        return []
    tail: list[str] = []
    size = 0
    for part in reversed(parts):
        if tail and size + len(part) > overlap:
            break
        tail.insert(0, part)
        size += len(part) + 1
    return tail


def _looks_like_heading(paragraph: str) -> bool:
    value = paragraph.strip()
    if not value or len(value) > 100:
        return False
    return bool(
        re.match(r"^(#{1,6}\s+|\d+(\.\d+)*[\.、\s]|第[一二三四五六七八九十]+[章节])", value)
        or (value.isupper() and len(value.split()) < 12)
    )


def chunk_pages(pages: list[PageText], chunk_size: int, overlap: int) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    current_section = ""
    index = 0

    for page in pages:
        text = _clean(page.text)
        if not text:
            continue
        paragraphs = _split_units(text, chunk_size)
        buffer_parts: list[str] = []

        for paragraph in paragraphs:
            if _looks_like_heading(paragraph):
                current_section = paragraph.lstrip("# ")[:500]

            buffer_length = sum(len(part) + 1 for part in buffer_parts)
            if buffer_parts and buffer_length + len(paragraph) > chunk_size:
                buffer = "\n".join(buffer_parts)
                chunks.append(ParsedChunk(buffer, page.page_number, current_section, index))
                index += 1
                buffer_parts = _overlap_tail(buffer_parts, overlap)
                buffer_parts.append(paragraph)
            else:
                buffer_parts.append(paragraph)

        if buffer_parts:
            chunks.append(ParsedChunk("\n".join(buffer_parts), page.page_number, current_section, index))
            index += 1

    return chunks


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)

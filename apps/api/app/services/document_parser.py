import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader


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
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n|(?<=[。！？.!?])\s+", text) if part.strip()]
        buffer = ""

        for paragraph in paragraphs:
            if _looks_like_heading(paragraph):
                current_section = paragraph.lstrip("# ")[:500]

            if buffer and len(buffer) + len(paragraph) + 1 > chunk_size:
                chunks.append(ParsedChunk(buffer, page.page_number, current_section, index))
                index += 1
                tail = buffer[-overlap:] if overlap else ""
                buffer = f"{tail}\n{paragraph}".strip()
            else:
                buffer = f"{buffer}\n{paragraph}".strip()

        if buffer:
            chunks.append(ParsedChunk(buffer, page.page_number, current_section, index))
            index += 1

    return chunks


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)

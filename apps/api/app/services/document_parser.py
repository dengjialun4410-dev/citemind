import re
import math
from collections import Counter
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


def _looks_like_author_metadata(line: str, title_started: bool) -> bool:
    value = re.sub(r"\s+", " ", line).strip()
    lowered = value.lower()
    if not value:
        return False
    if "@" in value or "anonymous submission" in lowered:
        return True
    if re.search(r"\b(?:school|department|faculty|institute|laboratory) of\b|\buniversity\b", lowered):
        return True
    name_pairs = len(re.findall(r"\b[A-Z][A-Za-z'-]+\s+[A-Z][A-Za-z'.-]+\d*", value))
    if name_pairs >= 2 and (value.count(",") >= 1 or re.search(r"\d", value)):
        return True
    if title_started and name_pairs == 1 and len(value.split()) <= 5 and not re.search(r"[:?!]", value):
        return True
    return False


def extract_document_title(pages: list[PageText], fallback_name: str) -> str:
    """Extract a human-readable paper title from the first page.

    The filename is retained only when the document does not expose a reliable
    title, such as some detached supplementary-material PDFs.
    """
    if not pages:
        return fallback_name
    markdown_heading = next(
        (re.sub(r"^#{1,6}\s*", "", line.strip()) for line in pages[0].text.splitlines() if re.match(r"^#{1,6}\s+", line.strip())),
        None,
    )
    if markdown_heading:
        return markdown_heading[:255]
    lines = [re.sub(r"\s+", " ", line).strip().lstrip("# ") for line in pages[0].text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return fallback_name

    first_lines = " ".join(lines[:4]).lower()
    if "supplementary material" in first_lines:
        acronym = next(
            (
                match.group(0)
                for line in lines[1:10]
                for match in [re.search(r"\b[A-Z][A-Z0-9]*-[A-Z0-9-]{2,}\b", line)]
                if match
            ),
            None,
        )
        return f"{acronym} Supplementary Material" if acronym else fallback_name

    title_lines: list[str] = []
    for line in lines[:14]:
        lowered = line.lower().strip(": ")
        if lowered in {"abstract", "摘要", "keywords", "key words"}:
            break
        if _looks_like_author_metadata(line, bool(title_lines)):
            if title_lines:
                break
            continue
        if len(line) > 240:
            break
        title_lines.append(line)
        if len(title_lines) >= 3 or sum(len(item) for item in title_lines) >= 230:
            break

    if not title_lines:
        return fallback_name
    title = " ".join(title_lines)
    title = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", title)
    title = re.sub(r"\s+", " ", title).strip(" ._-")
    if len(title) < 4 or len(title) > 255:
        return fallback_name
    return title


def extract_pages(content: bytes, extension: str) -> list[PageText]:
    extension = extension.lower()
    if extension == ".pdf":
        reader = PdfReader(BytesIO(content))
        pages = [PageText(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]
        return _strip_repeated_margins(pages)
    if extension == ".docx":
        document = DocxDocument(BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return [PageText(1, text)]
    if extension in {".txt", ".md"}:
        return [PageText(1, content.decode("utf-8", errors="replace"))]
    raise ValueError(f"暂不支持 {extension or '未知'} 格式")


def _normalized_margin_line(line: str) -> str:
    value = re.sub(r"\s+", " ", line).strip().lower()
    value = re.sub(r"\b\d+\b", "#", value)
    return value.strip("-|·• ")


def _is_page_decoration(line: str) -> bool:
    value = line.strip()
    lowered = value.lower()
    if not value or re.fullmatch(r"(?:page\s*)?\d+(?:\s*/\s*\d+)?", lowered):
        return True
    return bool(
        re.search(
            r"anonymous submission|copyright|all rights reserved|doi\s*:|"
            r"proceedings of|journal of|conference on|arxiv\s*:\s*\d+|"
            r"vol(?:ume)?\.?\s*\d+\s*(?:,|issue)",
            lowered,
        )
    )


def _is_strong_page_decoration(line: str) -> bool:
    """Publisher/review boilerplate is safe to remove even when PDF ordering puts it mid-page."""
    return bool(
        re.search(
            r"anonymi[sz]ed submission|review purposes only|distribution, citation, or public sharing|"
            r"copyright and publication details|copyright\s*(?:©|\(c\)|\d{4})|all rights reserved|"
            r"^\*?corresponding author\.?$",
            line.strip().lower(),
        )
    )


def _strip_repeated_margins(pages: list[PageText], boundary_lines: int = 5) -> list[PageText]:
    """Remove repeated header/footer lines without touching page body text."""
    if not pages:
        return pages
    boundary_keys: Counter[str] = Counter()
    page_lines: list[list[str]] = []
    for page in pages:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        page_lines.append(lines)
        boundaries = lines[:boundary_lines] + lines[-boundary_lines:]
        boundary_keys.update(set(filter(None, (_normalized_margin_line(line) for line in boundaries))))
    repeat_threshold = max(2, math.ceil(len(pages) * 0.5))
    repeated = {key for key, count in boundary_keys.items() if count >= repeat_threshold and len(key) >= 3}

    cleaned: list[PageText] = []
    for page, lines in zip(pages, page_lines):
        last_index = len(lines) - 1
        kept = []
        for index, line in enumerate(lines):
            is_boundary = index < boundary_lines or index > last_index - boundary_lines
            repeated_boundary = is_boundary and _normalized_margin_line(line) in repeated
            decoration = _is_page_decoration(line) and (is_boundary or "anonymous submission" in line.lower())
            if not repeated_boundary and not decoration and not _is_strong_page_decoration(line):
                kept.append(line)
        cleaned.append(PageText(page.page_number, "\n".join(kept)))
    return cleaned


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)
    text = re.sub(r"(?<=[a-z,;:])\n(?=[a-z])", " ", text)
    text = re.sub(r"(?<=[a-z])\n(?=[A-Z][a-z]+\s)", " ", text)
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

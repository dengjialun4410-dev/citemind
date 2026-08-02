import re

from ..models import Chunk, Document


ASPECTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("研究问题", ("however", "challenge", "problem", "limitation", "existing methods", "fail to")),
    ("核心方法", ("we propose", "we present", "we introduce", "our method", "framework", "architecture")),
    ("数据集与指标", ("dataset", "ntu", "kinetics", "accuracy", "top-1", "f1", "metric", "benchmark")),
    ("主要发现", ("achieves", "outperform", "results", "improve", "state-of-the-art", "superior")),
    ("局限与未来工作", ("limitation", "limitations", "future work", "drawback", "remains challenging")),
)


def _sentences(content: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", content).strip()
    return [item.strip() for item in re.split(r"(?<=[.!?。！？])\s+", normalized) if len(item.strip()) >= 30]


def _best_chunk(chunks: list[Chunk], signals: tuple[str, ...]) -> Chunk | None:
    ranked = sorted(
        chunks,
        key=lambda chunk: sum(signal in chunk.content.lower() for signal in signals),
        reverse=True,
    )
    if not ranked or not any(signal in ranked[0].content.lower() for signal in signals):
        return None
    return ranked[0]


def _excerpt(chunk: Chunk | None, signals: tuple[str, ...]) -> str:
    if not chunk:
        return "文档中未检索到明确表述。"
    candidates = _sentences(chunk.content)
    if not candidates:
        return re.sub(r"\s+", " ", chunk.content).strip()[:360]
    selected = max(candidates, key=lambda item: sum(signal in item.lower() for signal in signals))
    return selected[:360]


def _evidence(chunk: Chunk | None) -> list[dict[str, object]]:
    if not chunk:
        return []
    return [{"page_number": chunk.page_number, "section": chunk.section_path, "quote": re.sub(r"\s+", " ", chunk.content).strip()[:420]}]


def build_reading_card(document: Document, chunks: list[Chunk]) -> dict[str, object]:
    values: dict[str, str] = {}
    evidence: list[dict[str, object]] = []
    for label, signals in ASPECTS:
        chunk = _best_chunk(chunks, signals)
        values[label] = _excerpt(chunk, signals)
        evidence.extend(_evidence(chunk))
    overview = values["核心方法"]
    if overview == "文档中未检索到明确表述。":
        overview = _excerpt(chunks[0] if chunks else None, ())
    unique_evidence = list({(item["page_number"], item["quote"]): item for item in evidence}.values())[:5]
    return {
        "document_id": document.id,
        "document_name": document.name,
        "overview": overview,
        "research_question": values["研究问题"],
        "method": values["核心方法"],
        "datasets_and_metrics": values["数据集与指标"],
        "findings": values["主要发现"],
        "limitations": values["局限与未来工作"],
        "evidence": unique_evidence,
    }


def build_comparison(documents: list[Document], chunks_by_document: dict[int, list[Chunk]]) -> dict[str, object]:
    rows = []
    evidence: dict[int, list[dict[str, object]]] = {}
    for label, signals in ASPECTS:
        values = []
        for document in documents:
            chunk = _best_chunk(chunks_by_document.get(document.id, []), signals)
            values.append(_excerpt(chunk, signals))
            evidence.setdefault(document.id, []).extend(_evidence(chunk))
        rows.append({"label": label, "values": values})
    return {
        "document_ids": [document.id for document in documents],
        "document_names": [document.name for document in documents],
        "rows": rows,
        "evidence": {
            document_id: list({(item["page_number"], item["quote"]): item for item in items}.values())[:4]
            for document_id, items in evidence.items()
        },
    }

import math
import re
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Chunk, Document
from .embeddings import get_embedder


@dataclass
class SearchHit:
    chunk: Chunk
    document_name: str
    score: float


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _terms(text: str) -> set[str]:
    latin = re.findall(r"[a-zA-Z0-9_]{2,}", text.lower())
    chinese_sequences = re.findall(r"[\u4e00-\u9fff]+", text)
    chinese_bigrams = [
        sequence[index : index + 2]
        for sequence in chinese_sequences
        for index in range(max(0, len(sequence) - 1))
    ]
    return set(latin + chinese_bigrams)


def expand_query(query: str) -> str:
    """Add compact English research terms for zero-key cross-language retrieval."""
    expansions: list[str] = []
    intent_terms = [
        (("数据集", "评价指标", "评估指标", "实验指标", "准确率"), "dataset benchmark evaluation metric accuracy top-1 top-5"),
        (("局限", "不足", "缺点", "问题", "未来工作"), "limitation limitations weakness drawback future work however"),
        (("贡献", "创新", "研究问题", "核心问题", "总结", "讲了什么"), "abstract introduction contribution contributions propose proposed method conclusion"),
        (("方法", "模型", "模块", "架构", "怎么做"), "method methodology architecture module framework model pipeline"),
        (("结果", "性能", "效果", "提升"), "results performance improvement outperform experiment"),
    ]
    for triggers, english_terms in intent_terms:
        if any(trigger in query for trigger in triggers):
            expansions.append(english_terms)
    return f"{query} {' '.join(expansions)}".strip()


def _intent_bonus(query: str, content: str, page_number: int) -> float:
    lowered = content.lower()
    bonus = 0.0
    if any(term in query for term in ("数据集", "评价指标", "评估指标", "实验指标")):
        signals = ("dataset", "ntu rgb", "kinetics", "top-1", "top-5", "accuracy", "metric")
        bonus += min(0.2, sum(0.035 for signal in signals if signal in lowered))
    if any(term in query for term in ("贡献", "创新", "研究问题", "核心问题", "总结", "讲了什么")):
        if page_number == 1:
            bonus += 0.1
        if "abstract" in lowered:
            bonus += 0.1
        if "contribution" in lowered or "we propose" in lowered or "we develop" in lowered:
            bonus += 0.06
    if any(term in query for term in ("局限", "不足", "缺点", "未来工作")):
        if "limitation" in lowered or "future work" in lowered or "drawback" in lowered:
            bonus += 0.2
    return bonus


def _lexical_score(query: str, content: str) -> float:
    query_terms = _terms(query)
    if not query_terms:
        return 0.0
    content_terms = _terms(content)
    return len(query_terms & content_terms) / math.sqrt(len(query_terms) * max(1, len(content_terms)))


async def search(
    db: Session,
    knowledge_base_id: int,
    query: str,
    top_k: int,
    document_ids: list[int] | None = None,
) -> tuple[list[SearchHit], int]:
    started = perf_counter()
    expanded_query = expand_query(query)
    query_embedding = await get_embedder().embed(expanded_query)
    hits = []
    base_query = (
        select(Chunk, Document.name)
        .join(Document, Chunk.document_id == Document.id)
        .where(Document.knowledge_base_id == knowledge_base_id, Document.status == "ready")
    )
    if document_ids:
        base_query = base_query.where(Document.id.in_(document_ids))
    if db.bind and db.bind.dialect.name == "postgresql":
        distance = Chunk.embedding.op("<=>")(query_embedding)
        rows = db.execute(
            base_query.add_columns(distance.label("distance"))
            .order_by(distance)
            .limit(max(top_k * 8, 40))
        ).all()
        candidates = [(chunk, document_name, max(0.0, 1.0 - float(vector_distance))) for chunk, document_name, vector_distance in rows]
    else:
        rows = db.execute(base_query).all()
        candidates = [
            (chunk, document_name, max(0.0, _cosine(query_embedding, chunk.embedding)))
            for chunk, document_name in rows
        ]

    for chunk, document_name, semantic in candidates:
        lexical = _lexical_score(expanded_query, f"{chunk.section_path}\n{chunk.content}")
        filename_stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", document_name.rsplit(".", 1)[0].lower())
        compact_query = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", query.lower())
        filename_bonus = 0.35 if len(filename_stem) >= 3 and filename_stem in compact_query else 0.0
        score = semantic * 0.58 + lexical * 0.42 + filename_bonus + _intent_bonus(query, chunk.content, chunk.page_number)
        hits.append(SearchHit(chunk=chunk, document_name=document_name, score=score))
    hits.sort(key=lambda hit: hit.score, reverse=True)
    elapsed_ms = round((perf_counter() - started) * 1000)
    return hits[:top_k], elapsed_ms

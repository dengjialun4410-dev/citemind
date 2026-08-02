import math
import re
from collections import Counter
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Chunk, Document
from ..config import get_settings
from .embeddings import get_embedder


@dataclass
class SearchHit:
    chunk: Chunk
    document_name: str
    score: float
    semantic_score: float = 0.0
    lexical_score: float = 0.0


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
        if "contribution" in lowered:
            bonus += 0.16
        if "we propose" in lowered or "we develop" in lowered or "we introduce" in lowered:
            bonus += 0.14
    if any(term in query for term in ("局限", "不足", "缺点", "未来工作")):
        if "limitation" in lowered or "future work" in lowered or "drawback" in lowered:
            bonus += 0.2
    if any(term in query for term in ("方法", "模型", "模块", "架构", "结构", "怎么做")):
        method_signals = ("architecture", "contains three components", "branch", "module", "block", "pipeline", "framework")
        bonus += min(0.24, sum(0.04 for signal in method_signals if signal in lowered))
    return bonus


def _reference_penalty(content: str, page_number: int) -> float:
    lowered = content.lower()
    year_count = len(re.findall(r"\b(?:19|20)\d{2}\b", lowered))
    bibliography_signals = sum(lowered.count(signal) for signal in ("arxiv preprint", "in proceedings", "transactions on"))
    if (year_count >= 5 and bibliography_signals >= 2) or lowered.lstrip().startswith("references"):
        return 0.75
    if page_number > 1 and year_count >= 8:
        return 0.35
    return 0.0


def _lexical_score(query: str, content: str) -> float:
    query_terms = _terms(query)
    if not query_terms:
        return 0.0
    content_terms = _terms(content)
    return len(query_terms & content_terms) / math.sqrt(len(query_terms) * max(1, len(content_terms)))


def _tokens(text: str) -> list[str]:
    latin = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_+.-]*", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    bigrams = [value[index : index + 2] for value in chinese for index in range(max(1, len(value) - 1))]
    return latin + bigrams


def _bm25_scores(query: str, contents: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """Small in-memory BM25 stage for personal knowledge bases."""
    query_tokens = list(dict.fromkeys(_tokens(query)))
    documents = [_tokens(content) for content in contents]
    if not query_tokens or not documents:
        return [0.0] * len(contents)
    average_length = sum(len(document) for document in documents) / max(1, len(documents))
    document_frequency = Counter(
        token for document in documents for token in set(document) if token in query_tokens
    )
    scores: list[float] = []
    for document in documents:
        frequencies = Counter(document)
        length_normalizer = k1 * (1 - b + b * len(document) / max(1.0, average_length))
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            inverse_frequency = math.log(1 + (len(documents) - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
            score += inverse_frequency * frequency * (k1 + 1) / (frequency + length_normalizer)
        scores.append(score)
    return scores


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [1.0 if high > 0 else 0.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def _content_fingerprint(content: str) -> set[str]:
    tokens = _tokens(content[:900])
    return set(tokens)


def _similarity_penalty(candidate: SearchHit, selected: list[SearchHit]) -> float:
    if not selected:
        return 0.0
    candidate_terms = _content_fingerprint(candidate.chunk.content)
    penalty = 0.0
    for existing in selected:
        existing_terms = _content_fingerprint(existing.chunk.content)
        overlap = len(candidate_terms & existing_terms) / max(1, len(candidate_terms | existing_terms))
        same_page = candidate.document_name == existing.document_name and candidate.chunk.page_number == existing.chunk.page_number
        adjacent = same_page and abs(candidate.chunk.chunk_index - existing.chunk.chunk_index) <= 1
        penalty = max(penalty, overlap + (0.18 if same_page else 0.0) + (0.12 if adjacent else 0.0))
    return penalty


def _select_diverse_hits(hits: list[SearchHit], top_k: int) -> list[SearchHit]:
    selected: list[SearchHit] = []
    remaining = hits[:]
    while remaining and len(selected) < top_k:
        if not selected:
            selected.append(remaining.pop(0))
            continue
        best_index, _ = max(
            enumerate(remaining),
            key=lambda item: item[1].score - 0.22 * _similarity_penalty(item[1], selected),
        )
        selected.append(remaining.pop(best_index))
    return selected


async def search(
    db: Session,
    knowledge_base_id: int,
    query: str,
    top_k: int,
    document_ids: list[int] | None = None,
) -> tuple[list[SearchHit], int]:
    started = perf_counter()
    settings = get_settings()
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
            .limit(max(top_k * 8, settings.retrieval_candidate_k))
        ).all()
        candidates = [(chunk, document_name, max(0.0, 1.0 - float(vector_distance))) for chunk, document_name, vector_distance in rows]
    else:
        rows = db.execute(base_query).all()
        candidates = [
            (chunk, document_name, max(0.0, _cosine(query_embedding, chunk.embedding)))
            for chunk, document_name in rows
        ]

    searchable_contents = [f"{chunk.section_path}\n{chunk.content}" for chunk, _, _ in candidates]
    semantic_scores = [semantic for _, _, semantic in candidates]
    bm25_scores = _bm25_scores(expanded_query, searchable_contents)
    normalized_semantic = _minmax(semantic_scores)
    normalized_bm25 = _minmax(bm25_scores)

    for index, (chunk, document_name, semantic) in enumerate(candidates):
        lexical = normalized_bm25[index]
        filename_stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", document_name.rsplit(".", 1)[0].lower())
        compact_query = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", query.lower())
        filename_bonus = 0.35 if len(filename_stem) >= 3 and filename_stem in compact_query else 0.0
        intent_bonus = _intent_bonus(query, chunk.content, chunk.page_number)
        score = (
            normalized_semantic[index] * 0.62
            + lexical * 0.28
            + filename_bonus
            + intent_bonus
            - _reference_penalty(chunk.content, chunk.page_number)
        )
        hits.append(
            SearchHit(
                chunk=chunk,
                document_name=document_name,
                score=score,
                semantic_score=semantic,
                lexical_score=bm25_scores[index],
            )
        )
    hits.sort(key=lambda hit: hit.score, reverse=True)
    elapsed_ms = round((perf_counter() - started) * 1000)
    return _select_diverse_hits(hits, top_k), elapsed_ms

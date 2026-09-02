import math
import re
from collections import Counter
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.orm import Session

from ..models import Chunk, Document
from ..config import get_settings
from .embeddings import get_embedder
from .index_signature import current_index_signature


@dataclass
class SearchHit:
    chunk: Chunk
    document_name: str
    score: float
    semantic_score: float = 0.0
    lexical_score: float = 0.0
    expanded_lexical_score: float = 0.0
    query_coverage: float = 0.0


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
        (("持久同调", "持续同调"), "persistent homology persistent-homology topological descriptor"),
        (("图卷积", "动态图"), "graph convolution dynamic graph adjacency"),
        (("骨架动作", "动作识别"), "skeleton action recognition skeletal motion"),
        (("拓扑", "拓扑结构"), "topology topological structure"),
        (("过平滑", "过度平滑"), "over-smoothing oversmoothing graph propagation"),
    ]
    for triggers, english_terms in intent_terms:
        if any(trigger in query for trigger in triggers):
            expansions.append(english_terms)
    return f"{query} {' '.join(expansions)}".strip()


def _passes_relevance_gate(hit: SearchHit, query: str) -> bool:
    """Reject unrelated candidates without blocking Chinese-to-English recall.

    The multilingual E5 model usually gives a relevant Chinese-query/English-
    passage pair a lower cosine score than a same-language pair.  Query
    expansion is therefore treated as real lexical evidence, while the raw
    semantic fallback uses a slightly lower threshold only for Chinese input.
    """
    if hit.lexical_score >= 0.35 or hit.expanded_lexical_score >= 0.35 or hit.query_coverage >= 0.18:
        return True
    semantic_threshold = 0.80 if re.search(r"[\u4e00-\u9fff]", query) else 0.86
    return hit.semantic_score >= semantic_threshold


def _intent_bonus(query: str, content: str, page_number: int, section_path: str = "") -> float:
    lowered = content.lower()
    lowered_section = section_path.lower()
    bonus = 0.0
    if any(term in query for term in ("什么是", "是什么意思", "定义", "解释")):
        definition_signals = ("defined as", "refers to", "summarizes", "means")
        bonus += min(0.4, sum(0.16 for signal in definition_signals if signal in lowered))
        if any(term in query for term in ("持久同调", "持续同调")) and (
            "persistent homology summarizes" in lowered or "persistent-homology summarizes" in lowered
        ):
            bonus += 0.45
    if any(term in query for term in ("数据集", "评价指标", "评估指标", "实验指标")):
        signals = ("dataset", "ntu rgb", "kinetics", "top-1", "top-5", "accuracy", "metric")
        bonus += min(0.2, sum(0.035 for signal in signals if signal in lowered))
    if any(term in query for term in ("贡献", "创新", "研究问题", "核心问题", "总结", "讲了什么")):
        if page_number == 1:
            bonus += 0.1
        if "abstract" in lowered:
            bonus += 0.1
        if "overview" in lowered or "overview" in lowered_section:
            bonus += 0.28
        if "contribution" in lowered:
            bonus += 0.16
        if "we propose" in lowered or "we develop" in lowered or "we introduce" in lowered:
            bonus += 0.14
        if any(signal in lowered_section for signal in ("abstract", "introduction", "conclusion", "摘要", "引言", "结论")):
            bonus += 0.12
    if any(term in query for term in ("局限", "不足", "缺点", "未来工作")):
        if "limitation" in lowered or "future work" in lowered or "drawback" in lowered:
            bonus += 0.2
        if any(signal in lowered_section for signal in ("limitation", "discussion", "conclusion", "局限", "讨论", "结论")):
            bonus += 0.14
    if any(term in query for term in ("方法", "模型", "模块", "架构", "结构", "怎么做")):
        method_signals = ("architecture", "contains three components", "branch", "module", "block", "pipeline", "framework")
        bonus += min(0.24, sum(0.04 for signal in method_signals if signal in lowered))
        if any(signal in lowered_section for signal in ("method", "methodology", "approach", "architecture", "方法", "模型")):
            bonus += 0.14
        if any(signal in lowered_section for signal in ("related work", "references", "相关工作", "参考文献")):
            bonus -= 0.22
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


def _postgres_or_tsquery(text_value: str) -> str:
    """Build a parameterized OR tsquery from already-tokenized safe lexemes."""
    safe_tokens = []
    for token in _tokens(text_value):
        cleaned = "".join(re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", token))
        if cleaned and cleaned not in safe_tokens:
            safe_tokens.append(cleaned)
    return " | ".join(safe_tokens) or "citemind_no_match"


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


def _reciprocal_rank_scores(values: list[float], rank_constant: int = 60) -> list[float]:
    """Turn incomparable raw scores into stable 0..1 rank scores.

    Equal values receive the same dense rank. Non-positive lexical scores remain
    zero so absent terms cannot gain relevance merely from list position.
    """
    positive_values = sorted({value for value in values if value > 0.0}, reverse=True)
    ranks = {value: rank for rank, value in enumerate(positive_values, start=1)}
    return [
        (rank_constant + 1) / (rank_constant + ranks[value]) if value > 0.0 else 0.0
        for value in values
    ]


def _query_coverage(query: str, content: str) -> float:
    query_terms = _terms(query)
    if not query_terms:
        return 0.0
    return len(query_terms & _terms(content)) / len(query_terms)


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
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == "ready",
            Document.index_signature == current_index_signature(settings),
        )
    )
    if document_ids:
        base_query = base_query.where(Document.id.in_(document_ids))
    if db.bind and db.bind.dialect.name == "postgresql":
        candidate_limit = max(top_k * 10, settings.retrieval_candidate_k)
        db.execute(text(f"SET LOCAL hnsw.ef_search = {max(1, settings.pgvector_hnsw_ef_search)}"))
        distance = Chunk.embedding.op("<=>")(query_embedding)
        vector_rows = db.execute(
            base_query.add_columns(distance.label("distance"))
            .order_by(distance)
            .limit(candidate_limit)
        ).all()
        candidate_map = {
            chunk.id: (chunk, document_name, max(0.0, 1.0 - float(vector_distance)))
            for chunk, document_name, vector_distance in vector_rows
        }

        # PostgreSQL production mode performs a real lexical recall alongside
        # vector recall instead of applying BM25 only to vector-selected rows.
        search_vector = func.to_tsvector(
            literal_column("'simple'"),
            func.coalesce(Chunk.section_path, "") + literal_column("' '") + Chunk.content,
        )
        ts_query = func.to_tsquery(literal_column("'simple'"), _postgres_or_tsquery(expanded_query))
        text_rank = func.ts_rank_cd(search_vector, ts_query)
        lexical_rows = db.execute(
            base_query.add_columns(text_rank.label("text_rank"))
            .where(search_vector.op("@@")(ts_query))
            .order_by(text_rank.desc())
            .limit(candidate_limit)
        ).all()
        for chunk, document_name, _ in lexical_rows:
            if chunk.id not in candidate_map:
                candidate_map[chunk.id] = (
                    chunk,
                    document_name,
                    max(0.0, _cosine(query_embedding, chunk.embedding)),
                )
        candidates = list(candidate_map.values())
    else:
        rows = db.execute(base_query).all()
        candidates = [
            (chunk, document_name, max(0.0, _cosine(query_embedding, chunk.embedding)))
            for chunk, document_name in rows
        ]

    searchable_contents = [f"{chunk.section_path}\n{chunk.content}" for chunk, _, _ in candidates]
    semantic_scores = [semantic for _, _, semantic in candidates]
    original_bm25_scores = _bm25_scores(query, searchable_contents)
    expanded_bm25_scores = _bm25_scores(expanded_query, searchable_contents)
    ranked_semantic = _reciprocal_rank_scores(semantic_scores)
    ranked_original_bm25 = _reciprocal_rank_scores(original_bm25_scores)
    ranked_expanded_bm25 = _reciprocal_rank_scores(expanded_bm25_scores)

    for index, (chunk, document_name, semantic) in enumerate(candidates):
        coverage = _query_coverage(query, searchable_contents[index])
        filename_stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", document_name.rsplit(".", 1)[0].lower())
        compact_query = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", query.lower())
        filename_bonus = 0.35 if len(filename_stem) >= 3 and filename_stem in compact_query else 0.0
        intent_bonus = _intent_bonus(query, chunk.content, chunk.page_number, chunk.section_path)
        score = (
            ranked_semantic[index] * 0.58
            + ranked_original_bm25[index] * 0.24
            + ranked_expanded_bm25[index] * 0.08
            + coverage * 0.12
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
                lexical_score=original_bm25_scores[index],
                expanded_lexical_score=expanded_bm25_scores[index],
                query_coverage=coverage,
            )
        )
    hits.sort(key=lambda hit: hit.score, reverse=True)
    elapsed_ms = round((perf_counter() - started) * 1000)
    relevant_hits = [hit for hit in hits if _passes_relevance_gate(hit, query)]
    return _select_diverse_hits(relevant_hits, top_k), elapsed_ms

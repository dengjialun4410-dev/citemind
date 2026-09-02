from app.main import cited_evidence_window
from app.models import Chunk
from app.services.document_parser import PageText, _strip_repeated_margins, chunk_pages
from app.services.generation import _dataset_answer, _definition_answer, _method_answer
from app.services.retrieval import (
    SearchHit,
    _bm25_scores,
    _intent_bonus,
    _passes_relevance_gate,
    _postgres_or_tsquery,
    _query_coverage,
    _reciprocal_rank_scores,
    _select_diverse_hits,
)
from app.services.text_cleaning import clean_display_text, clean_reader_text, is_display_noise, is_reader_noise, is_reference_block


def test_bm25_prioritizes_matching_research_evidence() -> None:
    scores = _bm25_scores(
        "dataset evaluation metric top-1 accuracy",
        [
            "We evaluate on NTU RGB+D and report Top-1 accuracy.",
            "The model contains three graph convolution blocks.",
        ],
    )
    assert scores[0] > scores[1]


def test_rank_fusion_is_stable_for_close_scores_and_ignores_zero_lexical_hits() -> None:
    ranked = _reciprocal_rank_scores([0.801, 0.800, 0.799, 0.0])
    assert ranked[0] == 1.0
    assert ranked[0] > ranked[1] > ranked[2] > ranked[3]
    assert ranked[3] == 0.0


def test_original_query_coverage_is_not_inflated_by_expansion_terms() -> None:
    content = "dataset benchmark evaluation metric accuracy"
    assert _query_coverage("本文的局限是什么", content) == 0.0
    assert _query_coverage("dataset accuracy", content) == 1.0


def test_postgres_tsquery_uses_safe_or_lexemes() -> None:
    value = _postgres_or_tsquery("top-1 accuracy 图卷积")
    assert " | " in value
    assert "top1" in value
    assert "图卷" in value


def test_chunking_keeps_overlap_on_complete_units() -> None:
    pages = [PageText(1, "First complete sentence. Second complete sentence. Third complete sentence.")]
    chunks = chunk_pages(pages, chunk_size=45, overlap=28)
    assert len(chunks) >= 2
    assert all(len(chunk.content) <= 90 for chunk in chunks)
    assert not chunks[1].content.startswith("plete")


def test_dataset_answer_is_structured_and_grounded() -> None:
    chunk = Chunk(
        id=1,
        document_id=1,
        content=(
            "We evaluate on NTU RGB+D 60, NTU RGB+D 120, and Kinetics-Skeleton.\n"
            "TD-GCN achieves 93.6% Top-1 accuracy and 97.9% Top-5 accuracy."
        ),
        page_number=5,
        section_path="Experiments",
        chunk_index=0,
        token_count=20,
        embedding=[0.0] * 384,
    )
    answer = _dataset_answer([SearchHit(chunk=chunk, document_name="paper.pdf", score=1.0)])
    assert "NTU RGB+D 60" in answer
    assert "Kinetics-Skeleton" in answer
    assert "Top-1 accuracy" in answer
    assert "论文报告的结果" in answer


def test_diverse_selection_reduces_adjacent_duplicate_chunks() -> None:
    chunks = [
        Chunk(id=1, document_id=1, content="same evidence about graph module and topology gate", page_number=3, section_path="", chunk_index=1, token_count=10, embedding=[0.0] * 384),
        Chunk(id=2, document_id=1, content="same evidence about graph module and topology gate repeated", page_number=3, section_path="", chunk_index=2, token_count=10, embedding=[0.0] * 384),
        Chunk(id=3, document_id=1, content="different evidence about evaluation datasets and metrics", page_number=5, section_path="", chunk_index=9, token_count=10, embedding=[0.0] * 384),
    ]
    hits = [
        SearchHit(chunk=chunks[0], document_name="paper.pdf", score=0.9),
        SearchHit(chunk=chunks[1], document_name="paper.pdf", score=0.88),
        SearchHit(chunk=chunks[2], document_name="paper.pdf", score=0.76),
    ]
    selected = _select_diverse_hits(hits, 2)
    assert [hit.chunk.id for hit in selected] == [1, 3]


def test_citations_only_persist_evidence_referenced_by_answer() -> None:
    chunks = [
        Chunk(id=index, document_id=1, content=f"evidence {index}", page_number=index, section_path="", chunk_index=index, token_count=2, embedding=[0.0] * 384)
        for index in range(1, 4)
    ]
    hits = [SearchHit(chunk=chunk, document_name="paper.pdf", score=0.9) for chunk in chunks]
    assert [hit.chunk.id for hit in cited_evidence_window("Answer based on [1] and [2].", hits)] == [1, 2]


def test_display_cleaning_hides_table_and_formula_noise() -> None:
    noisy = "2026) 93.6 97.9 90.8 93.0 52.5 76.4 σ[−k(s−sc)]"
    assert is_display_noise(noisy)
    assert "检测到表格或公式" in clean_display_text(noisy)


def test_reader_cleaning_keeps_prose_and_removes_formula_boilerplate() -> None:
    source = (
        "This paragraph explains the topology-guided method in readable prose.\n"
        "δi,j = TX t=1 wt ∥xt,i −x t,j∥2 ,(1)\n"
        "Distribution, citation, or public sharing of this manuscript is prohibited.\n"
        "The descriptor is injected into the dynamic graph backbone."
    )
    cleaned = clean_reader_text(source)
    assert "topology-guided method" in cleaned
    assert "dynamic graph backbone" in cleaned
    assert "δi,j" not in cleaned
    assert "public sharing" not in cleaned
    assert is_reader_noise("2 softplus(ρh,r) 2")
    assert is_reader_noise("School of Computer Science, Example University, London, UK")
    assert is_reader_noise("author@example.edu")
    assert is_reader_noise("N60XS-10% Figure 1: A comparison of several evaluation metrics.")
    assert "efficient" in clean_reader_text("The proposed module is an efﬁcient representation learner.")
    assert clean_reader_text("Prior work uses this benchmark (Smith et al.") == ""


def test_reader_hides_reference_list_blocks() -> None:
    references = (
        "References\nSmith, J.; Wang, H.; and Li, K. A useful model. In Proceedings of CVPR, 2021.\n"
        "Brown, T.; Green, A.; and White, P. Another method. Journal of Machine Learning Research, 2020.\n"
        "Chen, X.; Liu, Y.; and Wu, Z. Graph learning. arXiv preprint arXiv:2201.001, 2022."
    )
    assert is_reference_block(references)
    assert clean_reader_text(references) == ""


def test_repeated_page_headers_and_footers_are_removed() -> None:
    pages = [
        PageText(1, "Journal of Useful Research\nFirst page body.\n1"),
        PageText(2, "Journal of Useful Research\nSecond page body.\n2"),
        PageText(3, "Journal of Useful Research\nThird page body.\n3"),
    ]
    cleaned = _strip_repeated_margins(pages)
    assert all("Journal of Useful Research" not in page.text for page in cleaned)
    assert [page.text for page in cleaned] == ["First page body.", "Second page body.", "Third page body."]


def test_review_boilerplate_is_removed_even_when_mid_page() -> None:
    pages = [PageText(1, "Useful body.\nThis is an anonymized submission for review purposes only.\nMore useful body.")]
    cleaned = _strip_repeated_margins(pages)
    assert cleaned[0].text == "Useful body.\nMore useful body."


def test_relevance_gate_rejects_semantically_vague_zero_lexical_hit() -> None:
    chunk = Chunk(id=10, document_id=1, content="unrelated content", page_number=1, section_path="", chunk_index=0, token_count=2, embedding=[0.0] * 384)
    vague = SearchHit(chunk=chunk, document_name="paper.pdf", score=0.62, semantic_score=0.82, lexical_score=0.0)
    direct = SearchHit(chunk=chunk, document_name="paper.pdf", score=0.72, semantic_score=0.82, lexical_score=0.8)
    assert not _passes_relevance_gate(vague, "weather tomorrow")
    assert _passes_relevance_gate(direct, "weather tomorrow")


def test_relevance_gate_allows_cross_language_semantic_and_expanded_hits() -> None:
    chunk = Chunk(id=11, document_id=1, content="paper contribution", page_number=1, section_path="Abstract", chunk_index=0, token_count=2, embedding=[0.0] * 384)
    semantic = SearchHit(chunk=chunk, document_name="paper.pdf", score=0.7, semantic_score=0.82)
    expanded = SearchHit(chunk=chunk, document_name="paper.pdf", score=0.7, semantic_score=0.5, expanded_lexical_score=0.8)
    assert _passes_relevance_gate(semantic, "总结论文贡献")
    assert _passes_relevance_gate(expanded, "总结论文贡献")


def test_method_answer_prefers_author_method_over_related_work() -> None:
    related = Chunk(id=11, document_id=1, content="ST-GCN established a unified framework for skeleton recognition (Yan et al. 2018).", page_number=2, section_path="Related Work", chunk_index=1, token_count=12, embedding=[0.0] * 384)
    method = Chunk(id=12, document_id=1, content="We propose a topology-guided architecture with a persistent-homology branch and stage-aware gates.", page_number=3, section_path="Method", chunk_index=2, token_count=12, embedding=[0.0] * 384)
    answer = _method_answer([
        SearchHit(chunk=related, document_name="paper.pdf", score=1.0),
        SearchHit(chunk=method, document_name="paper.pdf", score=0.9),
    ])
    assert "topology-guided architecture" in answer
    assert "ST-GCN" not in answer


def test_definition_answer_prefers_explicit_definition_sentence() -> None:
    chunk = Chunk(id=13, document_id=1, content="Persistent homology summarizes topological structures across filtration scales. It is useful in graph learning.", page_number=2, section_path="Background", chunk_index=3, token_count=14, embedding=[0.0] * 384)
    answer = _definition_answer("什么是持久同调？", [SearchHit(chunk=chunk, document_name="paper.pdf", score=1.0)])
    assert "summarizes topological structures" in answer
    assert _intent_bonus("什么是持久同调？", chunk.content, 2) >= 0.6


def test_method_intent_prefers_method_section_over_related_work() -> None:
    content = "The architecture contains three components and a graph module."
    method_bonus = _intent_bonus("模型架构怎么做", content, 3, "Method / Architecture")
    related_bonus = _intent_bonus("模型架构怎么做", content, 2, "Related Work")
    assert method_bonus > related_bonus + 0.3

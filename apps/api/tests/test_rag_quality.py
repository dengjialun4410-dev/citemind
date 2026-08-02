from app.main import cited_evidence_window
from app.models import Chunk
from app.services.document_parser import PageText, chunk_pages
from app.services.generation import _dataset_answer
from app.services.retrieval import SearchHit, _bm25_scores, _select_diverse_hits


def test_bm25_prioritizes_matching_research_evidence() -> None:
    scores = _bm25_scores(
        "dataset evaluation metric top-1 accuracy",
        [
            "We evaluate on NTU RGB+D and report Top-1 accuracy.",
            "The model contains three graph convolution blocks.",
        ],
    )
    assert scores[0] > scores[1]


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
    assert "Top-1 Accuracy" in answer
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

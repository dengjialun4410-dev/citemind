from app.models import Chunk
from app.services.document_parser import PageText, chunk_pages
from app.services.generation import _dataset_answer
from app.services.retrieval import SearchHit, _bm25_scores


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

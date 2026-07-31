from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EvaluationDataset, EvaluationResult, EvaluationRun
from .retrieval import search


async def run_retrieval_evaluation(db: Session, dataset: EvaluationDataset, top_k: int) -> EvaluationRun:
    questions = list(dataset.questions)
    if not questions:
        raise ValueError("评测集至少需要一个问题")

    run = EvaluationRun(dataset_id=dataset.id, top_k=top_k)
    db.add(run)
    db.flush()
    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    latencies: list[int] = []
    hit_count = 0

    for question in questions:
        hits, latency_ms = await search(db, dataset.knowledge_base_id, question.question, top_k)
        retrieved = [hit.chunk.id for hit in hits]
        relevant = set(question.relevant_chunk_ids)
        matches = relevant.intersection(retrieved)
        recall = len(matches) / len(relevant) if relevant else 0.0
        precision = len(matches) / top_k
        reciprocal_rank = 0.0
        for rank, chunk_id in enumerate(retrieved, start=1):
            if chunk_id in relevant:
                reciprocal_rank = 1 / rank
                break
        if matches:
            hit_count += 1
        recalls.append(recall)
        precisions.append(precision)
        reciprocal_ranks.append(reciprocal_rank)
        latencies.append(latency_ms)
        db.add(
            EvaluationResult(
                run_id=run.id,
                question_id=question.id,
                retrieved_chunk_ids=retrieved,
                recall=recall,
                precision=precision,
                reciprocal_rank=reciprocal_rank,
                latency_ms=latency_ms,
            )
        )

    count = len(questions)
    run.recall_at_k = sum(recalls) / count
    run.precision_at_k = sum(precisions) / count
    run.mrr = sum(reciprocal_ranks) / count
    run.hit_rate = hit_count / count
    run.average_latency_ms = sum(latencies) / count
    db.commit()
    db.refresh(run)
    return run

from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Document
from app.services.retrieval import expand_query


def setup_module() -> None:
    Base.metadata.drop_all(bind=engine)


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": "demo@citemind.dev", "password": "CiteMind123!"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_end_to_end_document_chat_and_evaluation() -> None:
    with TestClient(app) as client:
        headers = auth_headers(client)
        observability = client.get("/api/observability/summary", headers=headers)
        assert observability.status_code == 200
        assert observability.json()["database_backend"] == "sqlite"
        assert "average_latency_ms" in observability.json()
        bases = client.get("/api/knowledge-bases", headers=headers)
        assert bases.status_code == 200
        knowledge_base_id = bases.json()[0]["id"]

        upload = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/documents",
            headers=headers,
            files={
                "file": (
                    "transformer.md",
                    "# Transformer\n\nTransformer 使用自注意力机制处理序列信息。它可以并行计算，并通过多头注意力学习不同关系。",
                    "text/markdown",
                )
            },
        )
        assert upload.status_code == 202
        assert upload.json()["status"] == "ready"
        assert upload.json()["needs_reindex"] is False
        assert upload.json()["chunk_count"] >= 1
        document_id = upload.json()["id"]

        reindex = client.post(f"/api/documents/{document_id}/reindex", headers=headers)
        assert reindex.status_code == 202
        assert reindex.json()["status"] == "ready"

        noise = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/documents",
            headers=headers,
            files={"file": ("cooking.md", "番茄炒蛋需要番茄、鸡蛋和食用油。", "text/markdown")},
        )
        assert noise.status_code == 202
        noise_id = noise.json()["id"]

        reading_card = client.get(f"/api/documents/{document_id}/reading-card", headers=headers)
        assert reading_card.status_code == 200
        assert reading_card.json()["document_name"] == "Transformer"
        assert reading_card.json()["method"]

        reader = client.get(f"/api/documents/{document_id}/reader", headers=headers)
        assert reader.status_code == 200
        assert reader.json()[0]["content"]

        translation = client.post("/api/translate", headers=headers, json={"text": "Transformer uses self-attention."})
        assert translation.status_code == 200
        assert translation.json()["mode"] in {"google-free", "mymemory-free", "unavailable"}

        comparison = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/document-comparison",
            headers=headers,
            json=[document_id, noise_id],
        )
        assert comparison.status_code == 200
        assert comparison.json()["document_ids"] == [document_id, noise_id]
        assert len(comparison.json()["rows"]) == 5

        answer = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/chat",
            headers=headers,
            json={"question": "Transformer 使用什么机制处理序列？", "document_ids": [document_id]},
        )
        assert answer.status_code == 200
        body = answer.json()
        assert body["citations"]
        assert body["citations"][0]["document_name"] == "Transformer"
        assert {citation["document_name"] for citation in body["citations"]} == {"Transformer"}
        assert body["generation_mode"] == "local-extractive"
        assert body["confidence"] in {"high", "medium", "low"}
        assert 0 <= body["evidence_coverage"] <= 1

        unrelated = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/chat",
            headers=headers,
            json={"question": "请告诉我今天北京的天气", "document_ids": [document_id]},
        )
        assert unrelated.status_code == 200
        assert unrelated.json()["citations"] == []
        assert unrelated.json()["generation_mode"] == "relevance-rejection"
        assert "与当前知识库" in unrelated.json()["answer"]

        with client.stream(
            "POST",
            f"/api/knowledge-bases/{knowledge_base_id}/chat/stream",
            headers=headers,
            json={"question": "Transformer 的核心机制是什么？", "document_ids": [document_id]},
        ) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            payload_text = "".join(stream.iter_text())
            assert "event: delta" in payload_text
            assert "event: done" in payload_text
            assert '"citations"' in payload_text

        dataset = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/evaluation-datasets",
            headers=headers,
            json={"name": "基础检索集", "description": "端到端测试"},
        )
        assert dataset.status_code == 201
        dataset_id = dataset.json()["id"]
        question = client.post(
            f"/api/evaluation-datasets/{dataset_id}/questions",
            headers=headers,
            json={
                "question": "Transformer 使用什么机制处理序列？",
                "relevant_chunk_ids": [body["citations"][0]["chunk_id"]],
            },
        )
        assert question.status_code == 201
        run = client.post(
            f"/api/evaluation-datasets/{dataset_id}/runs",
            headers=headers,
            json={"top_k": 5},
        )
        assert run.status_code == 200
        assert run.json()["recall_at_k"] == 1.0
        assert run.json()["mrr"] == 1.0

        with SessionLocal() as db:
            stale_document = db.get(Document, document_id)
            assert stale_document is not None
            stale_document.index_signature = None
            db.commit()
        listed = client.get(f"/api/knowledge-bases/{knowledge_base_id}/documents", headers=headers)
        listed_document = next(item for item in listed.json() if item["id"] == document_id)
        assert listed_document["needs_reindex"] is True
        stale_chat = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/chat",
            headers=headers,
            json={"question": "Transformer 使用什么机制？", "document_ids": [document_id]},
        )
        assert stale_chat.status_code == 409
        assert "重建索引" in stale_chat.json()["detail"]
        refreshed = client.post(f"/api/documents/{document_id}/reindex", headers=headers)
        assert refreshed.status_code == 202
        assert refreshed.json()["needs_reindex"] is False


def test_auth_and_permissions() -> None:
    with TestClient(app) as client:
        assert client.get("/api/knowledge-bases").status_code == 401
        registered = client.post(
            "/api/auth/register",
            json={"email": "new@example.com", "password": "StrongPass123!", "name": "New User"},
        )
        assert registered.status_code == 201
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        assert client.get("/api/auth/me", headers=headers).json()["email"] == "new@example.com"
        assert client.get("/api/knowledge-bases", headers=headers).json() == []


def test_rejects_unsupported_document() -> None:
    with TestClient(app) as client:
        headers = auth_headers(client)
        knowledge_base_id = client.get("/api/knowledge-bases", headers=headers).json()[0]["id"]
        response = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/documents",
            headers=headers,
            files={"file": ("archive.zip", b"not a zip", "application/zip")},
        )
        assert response.status_code == 415


def test_chinese_research_query_expansion() -> None:
    expanded = expand_query("作者使用了哪些数据集和评价指标？")
    assert "dataset" in expanded
    assert "metric" in expanded
    assert "数据集" in expanded

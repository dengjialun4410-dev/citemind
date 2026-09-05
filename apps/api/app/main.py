import hashlib
import asyncio
import json
import re
from threading import Lock
from time import perf_counter
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .auth import create_access_token, get_current_user, hash_password, verify_password
from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .models import (
    Citation,
    Chunk,
    Conversation,
    Document,
    EvaluationDataset,
    EvaluationQuestion,
    EvaluationRun,
    KnowledgeBase,
    KnowledgeBaseMember,
    Message,
    User,
)
from .schemas import (
    ChatRequest,
    ChatResponse,
    CitationOut,
    ComparisonRowOut,
    ConversationDetailOut,
    ConversationSummaryOut,
    DocumentComparisonOut,
    DocumentOut,
    EvaluationDatasetCreate,
    EvaluationDatasetOut,
    EvaluationQuestionCreate,
    EvaluationRunOut,
    EvaluationRunRequest,
    HealthResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    LoginRequest,
    ObservabilityOut,
    ReadingCardOut,
    ReaderChunkOut,
    ResearchEvidenceOut,
    StoredMessageOut,
    TokenResponse,
    TranslationRequest,
    TranslationResponse,
    UserCreate,
    UserOut,
)
from .services.document_parser import safe_filename
from .services.document_processing import process_document
from .services.embeddings import get_embedder
from .services.generation import generate_answer
from .services.evaluation import run_retrieval_evaluation
from .services.retrieval import search
from .services.index_signature import current_index_signature
from .services.research_workspace import build_comparison, build_reading_card
from .services.translation import translate_to_chinese
from .services.text_cleaning import clean_display_text, clean_reader_text, is_reader_noise, is_reference_block
from .tasks import process_document_task

settings = get_settings()
_metrics_lock = Lock()
_request_metrics = {"count": 0, "errors": 0, "total_latency_ms": 0.0}


def document_out(document: Document) -> DocumentOut:
    needs_reindex = (
        document.status == "ready"
        and document.index_signature != current_index_signature(settings)
    )
    return DocumentOut.model_validate(document).model_copy(update={"needs_reindex": needs_reindex})


def summarize_evidence_quality(hits: list) -> tuple[str, float]:
    if not hits:
        return "low", 0.0
    unique_pages = {(hit.document_name, hit.chunk.page_number) for hit in hits}
    strong_hits = sum(1 for hit in hits if hit.score >= 0.55)
    coverage = min(
        1.0,
        len(unique_pages) / max(1, min(3, len(hits))) * 0.45
        + strong_hits / max(1, len(hits)) * 0.45
        + min(1.0, hits[0].score) * 0.10,
    )
    if coverage >= 0.72:
        return "high", round(coverage, 4)
    if coverage >= 0.42:
        return "medium", round(coverage, 4)
    return "low", round(coverage, 4)


def cited_evidence_window(answer: str, hits: list) -> list:
    cited_numbers = [int(value) for value in re.findall(r"\[(\d{1,2})\]", answer)]
    if not cited_numbers:
        return hits
    return hits[: min(len(hits), max(cited_numbers))]


def seed_demo_data(db: Session) -> None:
    if not settings.demo_seed_enabled:
        return
    user = db.scalar(select(User).where(User.email == settings.demo_user_email.lower()))
    if not user:
        user = User(
            email=settings.demo_user_email.lower(),
            name="研究者",
            password_hash=hash_password(settings.demo_user_password),
        )
        db.add(user)
        db.flush()
    knowledge_base = db.scalar(select(KnowledgeBase).order_by(KnowledgeBase.id))
    if not knowledge_base:
        knowledge_base = KnowledgeBase(
            name="科研论文库",
            description="上传论文后，获得带页码与原文证据的可信回答。",
        )
        db.add(knowledge_base)
        db.flush()
    membership = db.get(
        KnowledgeBaseMember,
        {"user_id": user.id, "knowledge_base_id": knowledge_base.id},
    )
    if not membership:
        db.add(KnowledgeBaseMember(user_id=user.id, knowledge_base_id=knowledge_base.id, role="owner"))
    db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_demo_data(db)
    await get_embedder(settings).embed("CiteMind retrieval warmup")
    yield


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def collect_request_metrics(request: Request, call_next):  # type: ignore[no-untyped-def]
    started = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        if request.url.path != "/health":
            with _metrics_lock:
                _request_metrics["count"] += 1
                _request_metrics["total_latency_ms"] += (perf_counter() - started) * 1000
                if status_code >= 400:
                    _request_metrics["errors"] += 1


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        model_mode="remote-llm" if settings.openai_api_key else "local-extractive",
        database_backend=engine.dialect.name,
        embedding_provider=settings.embedding_provider,
        task_mode="eager" if settings.celery_task_always_eager else "celery",
    )


@app.get("/api/observability/summary", response_model=ObservabilityOut)
def observability_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ObservabilityOut:
    del current_user
    with _metrics_lock:
        request_count = _request_metrics["count"]
        error_count = _request_metrics["errors"]
        average_latency_ms = _request_metrics["total_latency_ms"] / max(1, request_count)
    ready_document_count = db.scalar(select(func.count(Document.id)).where(Document.status == "ready")) or 0
    return ObservabilityOut(
        request_count=request_count,
        error_count=error_count,
        error_rate=round(error_count / max(1, request_count), 4),
        average_latency_ms=round(average_latency_ms, 2),
        database_backend=engine.dialect.name,
        task_mode="eager" if settings.celery_task_always_eager else "celery",
        ready_document_count=ready_document_count,
    )


@app.post("/api/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> TokenResponse:
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="该邮箱已经注册")
    user = User(email=email, name=payload.name.strip(), password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    return TokenResponse(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@app.get("/api/auth/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


def require_knowledge_base(db: Session, knowledge_base_id: int, user: User, write: bool = False) -> KnowledgeBase:
    membership = db.get(
        KnowledgeBaseMember,
        {"user_id": user.id, "knowledge_base_id": knowledge_base_id},
    )
    if not membership:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if write and membership.role not in {"owner", "editor"}:
        raise HTTPException(status_code=403, detail="你没有编辑该知识库的权限")
    knowledge_base = db.get(KnowledgeBase, knowledge_base_id)
    if not knowledge_base:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return knowledge_base


@app.get("/api/knowledge-bases", response_model=list[KnowledgeBaseOut])
def list_knowledge_bases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KnowledgeBaseOut]:
    rows = db.execute(
        select(KnowledgeBase, func.count(Document.id))
        .join(KnowledgeBaseMember)
        .outerjoin(Document)
        .where(KnowledgeBaseMember.user_id == current_user.id)
        .group_by(KnowledgeBase.id)
        .order_by(KnowledgeBase.created_at)
    ).all()
    return [KnowledgeBaseOut.model_validate(kb).model_copy(update={"document_count": count}) for kb, count in rows]


@app.post("/api/knowledge-bases", response_model=KnowledgeBaseOut, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeBaseOut:
    knowledge_base = KnowledgeBase(name=payload.name.strip(), description=payload.description.strip())
    db.add(knowledge_base)
    db.flush()
    db.add(KnowledgeBaseMember(user_id=current_user.id, knowledge_base_id=knowledge_base.id, role="owner"))
    db.commit()
    db.refresh(knowledge_base)
    return KnowledgeBaseOut.model_validate(knowledge_base)


@app.get("/api/knowledge-bases/{knowledge_base_id}/documents", response_model=list[DocumentOut])
def list_documents(
    knowledge_base_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DocumentOut]:
    require_knowledge_base(db, knowledge_base_id, current_user)
    documents = list(
        db.scalars(
            select(Document)
            .where(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.created_at.desc())
        )
    )
    return [document_out(document) for document in documents]


@app.get("/api/documents/{document_id}/reading-card", response_model=ReadingCardOut)
def get_reading_card(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    require_knowledge_base(db, document.knowledge_base_id, current_user)
    if document.status != "ready":
        raise HTTPException(status_code=409, detail="文档尚未完成解析")
    chunks = list(db.scalars(select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)))
    return build_reading_card(document, chunks)


@app.get("/api/documents/{document_id}/reader", response_model=list[ReaderChunkOut])
def get_document_reader(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReaderChunkOut]:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    require_knowledge_base(db, document.knowledge_base_id, current_user)
    if document.status != "ready":
        raise HTTPException(status_code=409, detail="文档尚未完成解析")
    chunks = db.scalars(select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index))
    result: list[ReaderChunkOut] = []
    in_references = False
    seen_reader_units: set[str] = set()
    for chunk in chunks:
        if re.search(r"(?:^|\n)\s*references\s*(?:\n|$)", chunk.content, re.IGNORECASE):
            in_references = True
        if in_references:
            continue
        if is_reference_block(chunk.content):
            continue
        content = clean_reader_text(chunk.content)
        if not content:
            continue
        unique_units: list[str] = []
        for unit in content.splitlines():
            fingerprint = re.sub(r"\W+", "", unit.lower())[:220]
            if not fingerprint or fingerprint in seen_reader_units:
                continue
            seen_reader_units.add(fingerprint)
            unique_units.append(unit)
        content = "\n".join(unique_units)
        if not content:
            continue
        section = "" if is_reader_noise(chunk.section_path) else clean_display_text(chunk.section_path, "")
        result.append(
            ReaderChunkOut(
                id=chunk.id,
                page_number=chunk.page_number,
                section=section,
                content=content,
            )
        )
    return result


@app.post("/api/translate", response_model=TranslationResponse)
async def translate(
    payload: TranslationRequest,
    current_user: User = Depends(get_current_user),
) -> TranslationResponse:
    del current_user
    translated_text, mode = await translate_to_chinese(payload.text, settings)
    return TranslationResponse(translated_text=translated_text, mode=mode)


@app.post("/api/knowledge-bases/{knowledge_base_id}/document-comparison", response_model=DocumentComparisonOut)
def compare_documents(
    knowledge_base_id: int,
    document_ids: list[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    require_knowledge_base(db, knowledge_base_id, current_user)
    selected_ids = list(dict.fromkeys(document_ids))
    if not 2 <= len(selected_ids) <= 5:
        raise HTTPException(status_code=422, detail="请选择 2 到 5 篇已完成解析的文档")
    documents = list(
        db.scalars(
            select(Document).where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.id.in_(selected_ids),
                Document.status == "ready",
            )
        )
    )
    if len(documents) != len(selected_ids):
        raise HTTPException(status_code=400, detail="对比范围包含不存在或尚未就绪的文档")
    ordered_documents = [next(document for document in documents if document.id == document_id) for document_id in selected_ids]
    chunks = list(db.scalars(select(Chunk).where(Chunk.document_id.in_(selected_ids)).order_by(Chunk.document_id, Chunk.chunk_index)))
    chunks_by_document = {document_id: [chunk for chunk in chunks if chunk.document_id == document_id] for document_id in selected_ids}
    return build_comparison(ordered_documents, chunks_by_document)


@app.post(
    "/api/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    knowledge_base_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentOut:
    require_knowledge_base(db, knowledge_base_id, current_user, write=True)
    original_name = safe_filename(file.filename or "document")
    extension = Path(original_name).suffix.lower()
    if extension not in {".pdf", ".txt", ".md", ".docx"}:
        raise HTTPException(status_code=415, detail="仅支持 PDF、DOCX、Markdown 和 TXT")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 25 MB")

    digest = hashlib.sha256(content).hexdigest()
    duplicate = db.scalar(
        select(Document).where(Document.knowledge_base_id == knowledge_base_id, Document.sha256 == digest)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="该文档已存在于知识库中")

    path = settings.upload_dir / f"{uuid4().hex}_{original_name}"
    path.write_bytes(content)
    document = Document(
        knowledge_base_id=knowledge_base_id,
        name=original_name,
        file_type=extension.lstrip("."),
        file_path=str(path),
        sha256=digest,
        status="processing",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    if settings.celery_task_always_eager:
        await process_document(document.id)
    else:
        process_document_task.delay(document.id)
    db.refresh(document)
    return document_out(document)


@app.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    require_knowledge_base(db, document.knowledge_base_id, current_user, write=True)
    path = Path(document.file_path)
    db.delete(document)
    db.commit()
    path.unlink(missing_ok=True)


@app.post("/api/documents/{document_id}/reindex", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def reindex_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentOut:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    require_knowledge_base(db, document.knowledge_base_id, current_user, write=True)
    if not Path(document.file_path).exists():
        raise HTTPException(status_code=410, detail="原始文档文件已丢失，无法重新索引")
    document.status = "processing"
    document.error_message = None
    db.commit()
    if settings.celery_task_always_eager:
        await process_document(document.id)
    else:
        process_document_task.delay(document.id)
    db.refresh(document)
    return document_out(document)


@app.get(
    "/api/knowledge-bases/{knowledge_base_id}/conversations",
    response_model=list[ConversationSummaryOut],
)
def list_conversations(
    knowledge_base_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ConversationSummaryOut]:
    require_knowledge_base(db, knowledge_base_id, current_user)
    rows = db.execute(
        select(
            Conversation,
            func.count(Message.id).label("message_count"),
            func.max(Message.created_at).label("last_message_at"),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.knowledge_base_id == knowledge_base_id)
        .group_by(Conversation.id)
        .order_by(func.coalesce(func.max(Message.created_at), Conversation.created_at).desc())
        .limit(30)
    ).all()
    return [
        ConversationSummaryOut(
            id=conversation.id,
            title=conversation.title,
            message_count=message_count,
            updated_at=last_message_at or conversation.created_at,
        )
        for conversation, message_count, last_message_at in rows
    ]


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationDetailOut:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    require_knowledge_base(db, conversation.knowledge_base_id, current_user)

    messages = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.id)
        )
    )
    message_ids = [message.id for message in messages]
    stored_citations = list(
        db.scalars(
            select(Citation)
            .where(Citation.message_id.in_(message_ids))
            .order_by(Citation.id)
        )
    ) if message_ids else []
    chunk_ids = {citation.chunk_id for citation in stored_citations}
    chunks_by_id = {
        chunk.id: chunk
        for chunk in db.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids)))
    } if chunk_ids else {}
    citations_by_message: dict[int, list[CitationOut]] = {}
    for citation in stored_citations:
        chunk = chunks_by_id.get(citation.chunk_id)
        citations_by_message.setdefault(citation.message_id, []).append(
            CitationOut(
                chunk_id=citation.chunk_id,
                document_name=citation.document_name,
                page_number=citation.page_number,
                section=chunk.section_path if chunk else "",
                quote=citation.quote,
                score=round(citation.score, 4),
            )
        )

    output_messages: list[StoredMessageOut] = []
    for message in messages:
        citations = citations_by_message.get(message.id, [])
        output_messages.append(
            StoredMessageOut(
                id=message.id,
                role=message.role,
                content=message.content,
                citations=citations,
                retrieval_ms=message.retrieval_ms or 0,
                generation_mode=message.generation_mode or "history",
                confidence=message.confidence or ("high" if citations else "low"),
                evidence_coverage=(
                    message.evidence_coverage
                    if message.evidence_coverage is not None
                    else (1.0 if citations else 0.0)
                ),
                created_at=message.created_at,
            )
        )
    return ConversationDetailOut(
        id=conversation.id,
        title=conversation.title,
        messages=output_messages,
    )


@app.delete("/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    require_knowledge_base(db, conversation.knowledge_base_id, current_user, write=True)
    db.delete(conversation)
    db.commit()


@app.post("/api/knowledge-bases/{knowledge_base_id}/chat", response_model=ChatResponse)
async def chat(
    knowledge_base_id: int,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatResponse:
    knowledge_base = require_knowledge_base(db, knowledge_base_id, current_user)
    index_signature = current_index_signature(settings)
    ready_count = db.scalar(
        select(func.count(Document.id)).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == "ready",
            Document.index_signature == index_signature,
        )
    )
    if not ready_count:
        stale_count = db.scalar(
            select(func.count(Document.id)).where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == "ready",
                or_(Document.index_signature.is_(None), Document.index_signature != index_signature),
            )
        )
        if stale_count:
            raise HTTPException(
                status_code=409,
                detail="文档索引由旧 Embedding 模型生成，请在右侧选择文档并点击“重建索引”后再提问",
            )
        raise HTTPException(status_code=400, detail="请先上传并成功解析至少一篇文档")

    conversation = db.get(Conversation, payload.conversation_id) if payload.conversation_id else None
    if conversation and conversation.knowledge_base_id != knowledge_base_id:
        raise HTTPException(status_code=400, detail="对话不属于当前知识库")
    if not conversation:
        conversation = Conversation(knowledge_base_id=knowledge_base.id, title=payload.question[:80])
        db.add(conversation)
        db.flush()

    db.add(Message(conversation_id=conversation.id, role="user", content=payload.question))
    if payload.document_ids:
        valid_document_ids = set(
            db.scalars(
                select(Document.id).where(
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.id.in_(payload.document_ids),
                    Document.status == "ready",
                    Document.index_signature == index_signature,
                )
            )
        )
        if valid_document_ids != set(payload.document_ids):
            raise HTTPException(
                status_code=409,
                detail="所选文档尚未就绪或索引版本已过期，请先点击“重建索引”",
            )
    hits, retrieval_ms = await search(
        db,
        knowledge_base_id,
        payload.question,
        payload.top_k,
        payload.document_ids,
    )
    try:
        answer, generation_mode = await generate_answer(payload.question, hits, settings)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"模型服务请求失败：{exc}") from exc

    assistant_message = Message(conversation_id=conversation.id, role="assistant", content=answer)
    db.add(assistant_message)
    db.flush()
    cited_hits = cited_evidence_window(answer, hits)
    citations: list[CitationOut] = []
    for hit in cited_hits:
        quote = clean_display_text(hit.chunk.content)[:420]
        db.add(
            Citation(
                message_id=assistant_message.id,
                chunk_id=hit.chunk.id,
                document_name=hit.document_name,
                page_number=hit.chunk.page_number,
                quote=quote,
                score=hit.score,
            )
        )
        citations.append(
            CitationOut(
                chunk_id=hit.chunk.id,
                document_name=hit.document_name,
                page_number=hit.chunk.page_number,
                section=hit.chunk.section_path,
                quote=quote,
                score=round(hit.score, 4),
            )
        )
    confidence, evidence_coverage = summarize_evidence_quality(cited_hits)
    assistant_message.retrieval_ms = retrieval_ms
    assistant_message.generation_mode = generation_mode
    assistant_message.confidence = confidence
    assistant_message.evidence_coverage = evidence_coverage
    db.commit()
    return ChatResponse(
        conversation_id=conversation.id,
        answer=answer,
        citations=citations,
        retrieval_ms=retrieval_ms,
        generation_mode=generation_mode,
        confidence=confidence,
        evidence_coverage=evidence_coverage,
    )


@app.post("/api/knowledge-bases/{knowledge_base_id}/chat/stream")
async def stream_chat(
    knowledge_base_id: int,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    result = await chat(knowledge_base_id, payload, db, current_user)

    def event(name: str, data: object) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate():  # type: ignore[no-untyped-def]
        yield event(
            "metadata",
            {"conversation_id": result.conversation_id, "retrieval_ms": result.retrieval_ms},
        )
        chunk_size = 28
        for start in range(0, len(result.answer), chunk_size):
            yield event("delta", {"text": result.answer[start : start + chunk_size]})
            await asyncio.sleep(0.01)
        yield event("done", result.model_dump(mode="json"))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get(
    "/api/knowledge-bases/{knowledge_base_id}/evaluation-datasets",
    response_model=list[EvaluationDatasetOut],
)
def list_evaluation_datasets(
    knowledge_base_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[EvaluationDatasetOut]:
    require_knowledge_base(db, knowledge_base_id, current_user)
    rows = db.execute(
        select(EvaluationDataset, func.count(EvaluationQuestion.id))
        .outerjoin(EvaluationQuestion)
        .where(EvaluationDataset.knowledge_base_id == knowledge_base_id)
        .group_by(EvaluationDataset.id)
        .order_by(EvaluationDataset.created_at.desc())
    ).all()
    return [
        EvaluationDatasetOut.model_validate(dataset).model_copy(update={"question_count": count})
        for dataset, count in rows
    ]


@app.post(
    "/api/knowledge-bases/{knowledge_base_id}/evaluation-datasets",
    response_model=EvaluationDatasetOut,
    status_code=status.HTTP_201_CREATED,
)
def create_evaluation_dataset(
    knowledge_base_id: int,
    payload: EvaluationDatasetCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvaluationDatasetOut:
    require_knowledge_base(db, knowledge_base_id, current_user, write=True)
    dataset = EvaluationDataset(
        knowledge_base_id=knowledge_base_id,
        name=payload.name.strip(),
        description=payload.description.strip(),
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return EvaluationDatasetOut.model_validate(dataset)


@app.post(
    "/api/evaluation-datasets/{dataset_id}/questions",
    status_code=status.HTTP_201_CREATED,
)
def add_evaluation_question(
    dataset_id: int,
    payload: EvaluationQuestionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int]:
    dataset = db.get(EvaluationDataset, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="评测集不存在")
    require_knowledge_base(db, dataset.knowledge_base_id, current_user, write=True)
    valid_chunk_ids = set(
        db.scalars(
            select(Chunk.id)
            .join(Document)
            .where(
                Document.knowledge_base_id == dataset.knowledge_base_id,
                Chunk.id.in_(payload.relevant_chunk_ids),
            )
        )
    )
    if valid_chunk_ids != set(payload.relevant_chunk_ids):
        raise HTTPException(status_code=400, detail="相关证据块不属于当前知识库或不存在")
    if not valid_chunk_ids:
        raise HTTPException(status_code=400, detail="知识库中还没有已索引文档")
    question = EvaluationQuestion(
        dataset_id=dataset.id,
        question=payload.question,
        relevant_chunk_ids=list(dict.fromkeys(payload.relevant_chunk_ids)),
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    return {"id": question.id}


@app.post("/api/evaluation-datasets/{dataset_id}/runs", response_model=EvaluationRunOut)
async def run_evaluation(
    dataset_id: int,
    payload: EvaluationRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvaluationRun:
    dataset = db.get(EvaluationDataset, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="评测集不存在")
    require_knowledge_base(db, dataset.knowledge_base_id, current_user, write=True)
    try:
        return await run_retrieval_evaluation(db, dataset, payload.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/evaluation-datasets/{dataset_id}/runs", response_model=list[EvaluationRunOut])
def list_evaluation_runs(
    dataset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[EvaluationRun]:
    dataset = db.get(EvaluationDataset, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="评测集不存在")
    require_knowledge_base(db, dataset.knowledge_base_id, current_user)
    return list(
        db.scalars(
            select(EvaluationRun)
            .where(EvaluationRun.dataset_id == dataset_id)
            .order_by(EvaluationRun.created_at.desc())
        )
    )

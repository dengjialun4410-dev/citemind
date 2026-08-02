import hashlib
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
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
    ReadingCardOut,
    ResearchEvidenceOut,
    TokenResponse,
    UserCreate,
    UserOut,
)
from .services.document_parser import safe_filename
from .services.document_processing import process_document
from .services.embeddings import get_embedder
from .services.generation import generate_answer
from .services.evaluation import run_retrieval_evaluation
from .services.retrieval import search
from .services.research_workspace import build_comparison, build_reading_card
from .tasks import process_document_task

settings = get_settings()


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


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        model_mode="remote-llm" if settings.openai_api_key else "local-extractive",
        database_backend=engine.dialect.name,
        embedding_provider=settings.embedding_provider,
        task_mode="eager" if settings.celery_task_always_eager else "celery",
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
) -> list[Document]:
    require_knowledge_base(db, knowledge_base_id, current_user)
    return list(
        db.scalars(
            select(Document)
            .where(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.created_at.desc())
        )
    )


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
) -> Document:
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
    return document


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
) -> Document:
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
    return document


@app.post("/api/knowledge-bases/{knowledge_base_id}/chat", response_model=ChatResponse)
async def chat(
    knowledge_base_id: int,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatResponse:
    knowledge_base = require_knowledge_base(db, knowledge_base_id, current_user)
    ready_count = db.scalar(
        select(func.count(Document.id)).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == "ready",
        )
    )
    if not ready_count:
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
                )
            )
        )
        if valid_document_ids != set(payload.document_ids):
            raise HTTPException(status_code=400, detail="检索范围包含不存在或尚未就绪的文档")
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
        quote = hit.chunk.content[:420]
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

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    name: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime
    document_count: int = 0


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    file_type: str
    status: str
    page_count: int
    chunk_count: int
    error_message: str | None
    created_at: datetime


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    conversation_id: int | None = None
    top_k: int = Field(default=5, ge=1, le=12)
    document_ids: list[int] | None = None


class CitationOut(BaseModel):
    chunk_id: int
    document_name: str
    page_number: int
    section: str
    quote: str
    score: float


class ChatResponse(BaseModel):
    conversation_id: int
    answer: str
    citations: list[CitationOut]
    retrieval_ms: int
    generation_mode: str


class HealthResponse(BaseModel):
    status: str
    model_mode: str
    database_backend: str
    embedding_provider: str
    task_mode: str


class EvaluationDatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)


class EvaluationQuestionCreate(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    relevant_chunk_ids: list[int] = Field(min_length=1)


class EvaluationDatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int
    name: str
    description: str
    question_count: int = 0
    created_at: datetime


class EvaluationRunRequest(BaseModel):
    top_k: int = Field(default=5, ge=1, le=20)


class EvaluationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    top_k: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    hit_rate: float
    average_latency_ms: float
    created_at: datetime

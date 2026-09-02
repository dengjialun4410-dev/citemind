import asyncio
from pathlib import Path

from sqlalchemy import delete

from ..config import get_settings
from ..database import SessionLocal
from ..models import Chunk, Document
from .document_parser import chunk_pages, extract_document_title, extract_pages
from .embeddings import get_embedder
from .index_signature import current_index_signature


async def process_document(document_id: int) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if not document:
            return
        document.status = "processing"
        document.error_message = None
        db.commit()
        try:
            path = Path(document.file_path)
            pages = extract_pages(path.read_bytes(), f".{document.file_type}")
            document.name = extract_document_title(pages, document.name)
            parsed_chunks = chunk_pages(pages, settings.chunk_size, settings.chunk_overlap)
            if not parsed_chunks:
                raise ValueError("未能从文档中提取有效文本")

            embedder = get_embedder(settings)
            vectors: list[list[float]] = []
            batch_size = 64
            for start in range(0, len(parsed_chunks), batch_size):
                batch = [item.content for item in parsed_chunks[start : start + batch_size]]
                vectors.extend(await embedder.embed_many(batch))

            db.execute(delete(Chunk).where(Chunk.document_id == document.id))
            for parsed, vector in zip(parsed_chunks, vectors):
                db.add(
                    Chunk(
                        document_id=document.id,
                        content=parsed.content,
                        page_number=parsed.page_number,
                        section_path=parsed.section_path,
                        chunk_index=parsed.chunk_index,
                        token_count=max(1, len(parsed.content) // 3),
                        embedding=vector,
                    )
                )
            document.page_count = len(pages)
            document.chunk_count = len(parsed_chunks)
            document.index_signature = current_index_signature(settings)
            document.status = "ready"
            db.commit()
        except Exception as exc:
            db.rollback()
            document = db.get(Document, document_id)
            if document:
                document.status = "failed"
                document.error_message = str(exc)[:1000]
                db.commit()
            raise


def process_document_sync(document_id: int) -> None:
    asyncio.run(process_document(document_id))

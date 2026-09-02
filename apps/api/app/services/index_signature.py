from ..config import Settings


PARSER_REVISION = "pdf-clean-v4"


def current_index_signature(settings: Settings) -> str:
    """Identify every setting that makes stored chunk vectors incompatible."""
    return ":".join(
        (
            settings.embedding_provider,
            settings.embedding_model,
            str(settings.embedding_dimensions),
            str(settings.chunk_size),
            str(settings.chunk_overlap),
            PARSER_REVISION,
        )
    )

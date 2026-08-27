from sentence_transformers import SentenceTransformer
import os


MODEL_NAME = "intfloat/multilingual-e5-small"

EMBEDDING_BATCH_SIZE = int(
    os.getenv("EMBEDDING_BATCH_SIZE", "32")
)


_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model

    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)

    return _model


def create_passage_embedding(
    text: str,
) -> list[float]:
    if not text or not text.strip():
        raise ValueError(
            "Text cannot be empty"
        )

    prepared_text = (
        f"passage: {text.strip()}"
    )

    model = get_model()

    embedding = model.encode(
        prepared_text,
        normalize_embeddings=True,
    )

    return embedding.tolist()


def create_query_embedding(
    text: str,
) -> list[float]:
    if not text or not text.strip():
        raise ValueError(
            "Query cannot be empty"
        )

    prepared_text = (
        f"query: {text.strip()}"
    )

    model = get_model()

    embedding = model.encode(
        prepared_text,
        normalize_embeddings=True,
    )

    return embedding.tolist()


def create_passage_embeddings(
    texts: list[str],
) -> list[list[float]]:
    if not texts:
        return []

    prepared_texts = []

    for text in texts:
        if not text or not text.strip():
            raise ValueError(
                "Passage text cannot be empty"
            )

        prepared_texts.append(
            f"passage: {text.strip()}"
        )

    model = get_model()

    embeddings = model.encode(
        prepared_texts,
        normalize_embeddings=True,
        batch_size=EMBEDDING_BATCH_SIZE,
        show_progress_bar=False,
    )

    return embeddings.tolist()
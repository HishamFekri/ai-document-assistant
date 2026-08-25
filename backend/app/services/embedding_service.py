from sentence_transformers import SentenceTransformer
import os


MODEL_NAME = "intfloat/multilingual-e5-small"

model = SentenceTransformer(MODEL_NAME)

EMBEDDING_BATCH_SIZE = int(
    os.getenv("EMBEDDING_BATCH_SIZE", "32")
)


def create_passage_embedding(text: str) -> list[float]:
    if not text or not text.strip():
        raise ValueError("Text cannot be empty")

    prepared_text = f"passage: {text.strip()}"

    embedding = model.encode(
        prepared_text,
        normalize_embeddings=True,
    )

    return embedding.tolist()


def create_query_embedding(text: str) -> list[float]:
    if not text or not text.strip():
        raise ValueError("Query cannot be empty")

    prepared_text = f"query: {text.strip()}"

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

    embeddings = model.encode(
        prepared_texts,
        normalize_embeddings=True,
        batch_size=EMBEDDING_BATCH_SIZE,
        show_progress_bar=False,
    )

    return embeddings.tolist()
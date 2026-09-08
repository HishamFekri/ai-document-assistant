"""Provider-free comparison of candidate IDs, before RAG reranking/deduplication."""


def compare_candidate_ids(exact_ids, approximate_ids, k):
    if k <= 0:
        raise ValueError("k must be positive")
    reference = set(exact_ids[:k])
    returned = set(approximate_ids[:k])
    overlap = len(reference & returned)
    return {
        "k": k,
        "exact_ids": list(exact_ids[:k]),
        "approximate_ids": list(approximate_ids[:k]),
        "exact_candidate_count": len(exact_ids),
        "approximate_candidate_count": len(approximate_ids),
        "top_k_overlap": overlap,
        "recall_at_k": overlap / len(reference) if reference else None,
    }

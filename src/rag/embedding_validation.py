"""Check an embedding response before publishing any index data."""
import math


def validate_vectors(vectors, expected_count: int, dimensions: int) -> None:
    if len(vectors) != expected_count or dimensions < 1:
        raise ValueError("Unexpected embedding count or dimensions.")
    for vector in vectors:
        if len(vector) != dimensions or not all(math.isfinite(v) for v in vector) or not any(vector):
            raise ValueError("The embedding model returned an invalid vector.")

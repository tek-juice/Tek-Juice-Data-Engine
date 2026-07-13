"""
DATA ENGINE — Cosine Similarity Engine
Vector Mathematics and Comparison Engine (Phase 2).
Computes similarity, distance, and relationship scores between vectors.
"""

import math
from typing import Union

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

Vector = list[float]
Matrix = list[list[float]]


def cosine_similarity(a: Vector, b: Vector) -> float:
    """
    Compute cosine similarity between two vectors.
    Returns a value in [-1.0, 1.0]. Higher = more similar.
    """
    if len(a) != len(b):
        raise ValueError(f"Vector dimension mismatch: {len(a)} vs {len(b)}")

    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(np.dot(va, vb) / (norm_a * norm_b))


def cosine_distance(a: Vector, b: Vector) -> float:
    """
    Compute cosine distance between two vectors.
    Returns a value in [0.0, 2.0]. Lower = more similar.
    """
    return 1.0 - cosine_similarity(a, b)


def euclidean_distance(a: Vector, b: Vector) -> float:
    """Euclidean (L2) distance between two vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    return float(np.linalg.norm(va - vb))


def dot_product_similarity(a: Vector, b: Vector) -> float:
    """Dot product similarity (for unit-normalised vectors this equals cosine similarity)."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    return float(np.dot(va, vb))


def pairwise_cosine_similarity(matrix_a: Matrix, matrix_b: Matrix) -> np.ndarray:
    """
    Compute pairwise cosine similarity between two sets of vectors.

    Args:
        matrix_a: Shape (m, d) — m vectors of dimension d.
        matrix_b: Shape (n, d) — n vectors of dimension d.

    Returns:
        NumPy array of shape (m, n) with similarity scores.
    """
    a = np.array(matrix_a, dtype=np.float32)
    b = np.array(matrix_b, dtype=np.float32)

    # Normalise rows
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-10)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)

    return a_norm @ b_norm.T


def average_vector(vectors: Matrix) -> Vector:
    """Compute element-wise average of a list of vectors."""
    if not vectors:
        raise ValueError("Cannot average empty vector list.")
    arr = np.array(vectors, dtype=np.float32)
    return arr.mean(axis=0).tolist()


def normalise_vector(v: Vector) -> Vector:
    """Return the unit-normalised version of a vector."""
    arr = np.array(v, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return v
    return (arr / norm).tolist()


def top_k_similar(
    query: Vector,
    candidates: list[tuple[str, Vector]],
    k: int = 10,
    threshold: float = 0.0,
) -> list[tuple[str, float]]:
    """
    Find top-k most similar vectors to a query from a list of candidates.

    Args:
        query:      Query vector.
        candidates: List of (id, vector) tuples.
        k:          Max results to return.
        threshold:  Minimum similarity score to include.

    Returns:
        List of (id, similarity) tuples sorted by descending similarity.
    """
    scored = [
        (cid, cosine_similarity(query, vec))
        for cid, vec in candidates
    ]
    filtered = [(cid, score) for cid, score in scored if score >= threshold]
    return sorted(filtered, key=lambda x: x[1], reverse=True)[:k]

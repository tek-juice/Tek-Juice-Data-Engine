"""
DATA ENGINE — Vector Clustering
Groups semantically similar vectors using K-Means and DBSCAN.
Phase 2: Pattern analysis for gap detection and semantic organisation.
"""

import numpy as np
import structlog
from dataclasses import dataclass

logger = structlog.get_logger(__name__)


@dataclass
class ClusterResult:
    """Result of a clustering operation."""
    n_clusters: int
    labels: list[int]          # Cluster label per input vector (-1 = noise/outlier)
    centroids: list[list[float]]
    cluster_sizes: dict[int, int]
    silhouette_score: float | None = None


def kmeans_cluster(
    vectors: list[list[float]],
    n_clusters: int,
    n_init: int = 10,
    max_iter: int = 300,
    random_state: int = 42,
) -> ClusterResult:
    """
    Cluster vectors using K-Means.

    Args:
        vectors:     List of embedding vectors.
        n_clusters:  Number of clusters to form.
        n_init:      Number of K-Means initialisations.
        max_iter:    Maximum iterations per run.
        random_state: Random seed for reproducibility.

    Returns:
        ClusterResult with labels, centroids, and cluster sizes.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    if len(vectors) < n_clusters:
        raise ValueError(f"Cannot form {n_clusters} clusters from {len(vectors)} vectors.")

    X = np.array(vectors, dtype=np.float32)

    km = KMeans(
        n_clusters=n_clusters,
        n_init=n_init,
        max_iter=max_iter,
        random_state=random_state,
    )
    labels = km.fit_predict(X)
    centroids = km.cluster_centers_.tolist()

    cluster_sizes = {
        int(label): int((labels == label).sum())
        for label in set(labels)
    }

    # Silhouette score requires at least 2 clusters and 2 samples per cluster
    sil = None
    if n_clusters >= 2 and len(vectors) > n_clusters:
        try:
            sil = float(silhouette_score(X, labels))
        except Exception:
            pass

    logger.debug("kmeans_clustering_complete", n_clusters=n_clusters, vectors=len(vectors))
    return ClusterResult(
        n_clusters=n_clusters,
        labels=labels.tolist(),
        centroids=centroids,
        cluster_sizes=cluster_sizes,
        silhouette_score=sil,
    )


def dbscan_cluster(
    vectors: list[list[float]],
    eps: float = 0.3,
    min_samples: int = 3,
) -> ClusterResult:
    """
    Cluster vectors using DBSCAN (density-based).
    Does not require specifying n_clusters upfront.
    Outlier vectors receive label -1.

    Args:
        vectors:     List of embedding vectors.
        eps:         Maximum cosine distance between neighbours.
        min_samples: Minimum points to form a cluster core.

    Returns:
        ClusterResult (centroids are computed per cluster from member vectors).
    """
    from sklearn.cluster import DBSCAN

    X = np.array(vectors, dtype=np.float32)
    # Normalise for cosine distance
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    X_norm = X / (norms + 1e-10)

    db = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine")
    labels = db.fit_predict(X_norm)

    unique_labels = set(labels)
    n_clusters = len(unique_labels - {-1})

    centroids: list[list[float]] = []
    cluster_sizes: dict[int, int] = {}
    for label in sorted(unique_labels):
        mask = labels == label
        cluster_sizes[int(label)] = int(mask.sum())
        if label != -1:
            centroid = X[mask].mean(axis=0).tolist()
            centroids.append(centroid)

    logger.debug(
        "dbscan_clustering_complete",
        n_clusters=n_clusters,
        outliers=cluster_sizes.get(-1, 0),
        vectors=len(vectors),
    )
    return ClusterResult(
        n_clusters=n_clusters,
        labels=labels.tolist(),
        centroids=centroids,
        cluster_sizes=cluster_sizes,
    )


def auto_cluster(
    vectors: list[list[float]],
    max_clusters: int = 10,
) -> ClusterResult:
    """
    Automatically determine optimal k using silhouette score,
    then run K-Means with the best k.
    """
    if len(vectors) < 4:
        return kmeans_cluster(vectors, n_clusters=1)

    best_k = 2
    best_score = -1.0

    for k in range(2, min(max_clusters + 1, len(vectors))):
        result = kmeans_cluster(vectors, n_clusters=k)
        if result.silhouette_score and result.silhouette_score > best_score:
            best_score = result.silhouette_score
            best_k = k

    logger.info("auto_cluster_selected_k", k=best_k, silhouette=round(best_score, 4))
    return kmeans_cluster(vectors, n_clusters=best_k)

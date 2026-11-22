"""T4 Embed: multilingual sentence embeddings, computed locally with fastembed.

POC simplification: a small local model (384 dimensions) instead of a managed
embedding model on Bedrock (DESIGN.md §6). No API key, no GPU needed.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding

from kb.config import EMBEDDING_MODEL


@lru_cache
def _model() -> TextEmbedding:
    return TextEmbedding(EMBEDDING_MODEL)


def embed(texts: list[str]) -> list[np.ndarray]:
    return list(_model().embed(texts)) if texts else []

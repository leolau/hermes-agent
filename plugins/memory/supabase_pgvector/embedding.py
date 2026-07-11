"""Deterministic, dependency-free text embeddings for the live memory tier.

The Supabase/pgvector provider stores an embedding vector alongside every
memory row so recall can be *semantic* (nearest-neighbour by meaning) rather
than a substring match. Embedding quality is pluggable, but the default must
work with **zero credentials and no network** so the provider is usable out of
the box and its tests are hermetic.

:class:`HashingEmbedder` hashes tokens into a fixed-dimension bag-of-words
vector and L2-normalises it. Two texts that share vocabulary land close under
cosine distance; disjoint texts are near-orthogonal. It is fully deterministic
(``sha256`` per token, not an RNG) so the same text always embeds identically
across processes and platforms — a property the round-trip and concurrency
tests rely on.

A real embedding model (self-hosted or provider-hosted) can be dropped in
later by implementing :class:`Embedder` and selecting it from ``config.yaml``;
nothing else in the store changes because everything speaks ``list[float]``.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List, Protocol, runtime_checkable

#: Embedding width. Small enough to keep rows cheap, wide enough that hashed
#: tokens rarely collide for realistic memory entries.
DEFAULT_DIM = 256

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    """Turns text into a fixed-length embedding vector."""

    @property
    def dim(self) -> int:
        """Dimension of every vector this embedder produces."""
        ...

    def embed(self, text: str) -> List[float]:
        """Return the embedding for ``text`` (length == :attr:`dim`)."""
        ...


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class HashingEmbedder:
    """Deterministic hashing embedder — the credential-free default.

    Tokens are lower-cased alphanumerics; each hashes to one dimension with a
    sign bit, the accumulated vector is L2-normalised. Empty / token-less text
    yields the zero vector (cosine-undefined but stored harmlessly).
    """

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        if dim <= 0:
            raise ValueError(f"Embedding dim must be positive, got {dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self._dim
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(component * component for component in vec))
        if norm > 0.0:
            vec = [component / norm for component in vec]
        return vec


def get_embedder(dim: int = DEFAULT_DIM) -> Embedder:
    """Return the configured embedder (currently the hashing default)."""
    return HashingEmbedder(dim=dim)

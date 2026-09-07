"""Scenario-scoped local provider backed by openJiuwen DeepSearch splitting."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from jindiao.application.errors import JindiaoError, ScenarioIntegrityError
from jindiao.scenarios import ScenarioSnapshot

from .interface import DeepSearchHit, DeepSearchQuery


class DeepSearchLifecycleError(JindiaoError):
    pass


@dataclass(frozen=True, slots=True)
class _CorpusChunk:
    path: str
    fragment: str
    content: str


def build_mock_ref(snapshot: ScenarioSnapshot, path: str, fragment: str) -> str:
    """Build a stable scenario/version/path/fragment reference after scope validation."""

    if path not in snapshot.available_paths or not path.startswith("corpus/"):
        raise ScenarioIntegrityError("DeepSearch reference path is outside the snapshot corpus")
    if not re.fullmatch(r"chunk-\d{4,}", fragment):
        raise ScenarioIntegrityError("DeepSearch fragment identifier is invalid")
    scenario = quote(snapshot.manifest.scenario_id, safe="")
    version = quote(snapshot.manifest.version, safe="")
    encoded_path = quote(path, safe="/")
    return f"mock://{scenario}/{version}/{encoded_path}#{fragment}"


def _split_with_deepsearch(
    *,
    document_id: str,
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[str, ...]:
    # Lazy imports avoid loading the full DeepSearch stack at API process import time.
    from openjiuwen.core.retrieval.common.document import Document
    from openjiuwen_deepsearch.algorithm.search_index.text_splitter import CharSplitter

    splitter = CharSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    document = Document(id_=document_id, text=text, metadata={})
    return tuple(chunk.text for chunk in splitter.split(document) if chunk.text.strip())


def _relevance(query: str, content: str) -> float:
    normalized_query = "".join(query.casefold().split())
    normalized_content = "".join(content.casefold().split())
    if normalized_query in normalized_content:
        return 1.0
    query_chars = {char for char in normalized_query if char.isalnum()}
    if not query_chars:
        return 0.0
    overlap = query_chars & {char for char in normalized_content if char.isalnum()}
    return len(overlap) / len(query_chars)


class ScenarioDeepSearchProvider:
    """Load and search only the `corpus/` files captured in one scenario snapshot."""

    def __init__(
        self,
        snapshot: ScenarioSnapshot,
        *,
        chunk_size: int = 400,
        chunk_overlap: int = 60,
    ) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be between zero and chunk_size")
        self._snapshot = snapshot
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._chunks: tuple[_CorpusChunk, ...] = ()
        self._is_open = False
        self._lock = asyncio.Lock()

    @property
    def scenario_snapshot_id(self) -> str:
        return self._snapshot.scenario_snapshot_id

    async def aopen(self) -> None:
        async with self._lock:
            if self._is_open:
                return
            chunks: list[_CorpusChunk] = []
            for path in self._snapshot.available_paths:
                if not path.startswith("corpus/"):
                    continue
                texts = _split_with_deepsearch(
                    document_id=f"{self.scenario_snapshot_id}:{path}",
                    text=self._snapshot.read_text(path),
                    chunk_size=self._chunk_size,
                    chunk_overlap=self._chunk_overlap,
                )
                chunks.extend(
                    _CorpusChunk(
                        path=path,
                        fragment=f"chunk-{index:04d}",
                        content=text,
                    )
                    for index, text in enumerate(texts, start=1)
                )
            self._chunks = tuple(chunks)
            self._is_open = True

    async def search(self, query: DeepSearchQuery) -> tuple[DeepSearchHit, ...]:
        if not self._is_open:
            raise DeepSearchLifecycleError("DeepSearch provider is not open")
        scored = [
            (score, chunk)
            for chunk in self._chunks
            if (score := _relevance(query.text, chunk.content)) > 0
        ]
        scored.sort(key=lambda item: (-item[0], item[1].path, item[1].fragment))
        hits: list[DeepSearchHit] = []
        for score, chunk in scored[: query.top_k]:
            identity = f"{self.scenario_snapshot_id}|{chunk.path}|{chunk.fragment}"
            hits.append(
                DeepSearchHit(
                    hit_id=f"hit-{hashlib.sha256(identity.encode()).hexdigest()[:24]}",
                    scenario_snapshot_id=self.scenario_snapshot_id,
                    path=chunk.path,
                    fragment=chunk.fragment,
                    content=chunk.content,
                    score=score,
                    raw_ref=build_mock_ref(self._snapshot, chunk.path, chunk.fragment),
                )
            )
        return tuple(hits)

    async def aclose(self) -> None:
        async with self._lock:
            self._chunks = ()
            self._is_open = False


class DeepSearchCustomLocalAdapter:
    """Translate the stable provider contract to DeepSearch custom-local result records."""

    def __init__(self, provider: ScenarioDeepSearchProvider, *, top_k: int = 5) -> None:
        if not 1 <= top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")
        self._provider = provider
        self._top_k = top_k

    async def aopen(self) -> None:
        await self._provider.aopen()

    async def aresults(self, query: str) -> list[dict[str, object]]:
        hits = await self._provider.search(DeepSearchQuery(text=query, top_k=self._top_k))
        return [
            {
                "knowledge_base_id": hit.scenario_snapshot_id,
                "file_id": f"{hit.path}#{hit.fragment}",
                "title": Path(hit.path).name,
                "content": hit.content,
                "score": hit.score,
                "raw_ref": hit.raw_ref,
            }
            for hit in hits
        ]

    async def aclose(self) -> None:
        await self._provider.aclose()


__all__ = [
    "DeepSearchCustomLocalAdapter",
    "DeepSearchLifecycleError",
    "ScenarioDeepSearchProvider",
    "build_mock_ref",
]

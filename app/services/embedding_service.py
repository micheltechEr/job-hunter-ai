import re
import hashlib
import logging
from typing import List, Dict
import numpy as np
from openai import AsyncOpenAI
from app.config import settings
from app.services.rate_limiter import execute_with_llm_protection

logger = logging.getLogger("job_hunter.embedding_service")


def _local_text_vector(text: str, dim: int = 256) -> List[float]:
    """Generates a deterministic normalized term-frequency embedding vector locally with 0 API calls."""
    if not text:
        return [0.0] * dim
    
    words = re.findall(r"\w+", text.lower())
    if not words:
        return [0.0] * dim
    
    vec = np.zeros(dim, dtype=np.float32)
    for word in words:
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16) % dim
        vec[h] += 1.0
    
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


class EmbeddingService:
    def __init__(self):
        api_key = settings.LLM_API_KEY or "placeholder-key"
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.LLM_BASE_URL
        )
        self.model = "text-embedding-3-small"
        self._embeddings_disabled = False
        # In-memory vector cache to eliminate redundant embedding API calls and accelerate ATS
        self._cache: Dict[str, List[float]] = {}

    def _get_cache_key(self, text: str) -> str:
        """Computes SHA-256 hash of normalized text for fast cache lookup."""
        normalized = " ".join(text.strip().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    async def get_embedding(self, text: str) -> List[float]:
        """Fetches vector embedding with in-memory cache and rate limit protection with automatic local fallback."""
        if not text or not text.strip():
            return [0.0] * 256

        cache_key = self._get_cache_key(text)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if self._embeddings_disabled or not settings.LLM_API_KEY:
            local_vec = _local_text_vector(text)
            self._cache[cache_key] = local_vec
            return local_vec

        async def _call_embedding(model_name: str):
            return await self.client.embeddings.create(
                input=[text[:8000]],
                model=model_name
            )

        try:
            response = await execute_with_llm_protection(_call_embedding, self.model)
            vec = response.data[0].embedding
            self._cache[cache_key] = vec
            return vec
        except Exception as e:
            # Switch permanently to local deterministic vectorizer on any credential/proxy/bad request failure
            self._embeddings_disabled = True
            logger.info(f"Embedding endpoint unavailable on proxy ({e}). Switched to local deterministic vectorizer permanently.")
            local_vec = _local_text_vector(text)
            self._cache[cache_key] = local_vec
            return local_vec

    @staticmethod
    def calculate_similarity(v1: List[float], v2: List[float]) -> float:
        """Computes cosine similarity between two vector lists."""
        if not v1 or not v2:
            return 0.0
        vec1 = np.array(v1, dtype=np.float32)
        vec2 = np.array(v2, dtype=np.float32)
        if len(vec1) != len(vec2):
            # Dimensions mismatch safeguard
            min_len = min(len(vec1), len(vec2))
            vec1 = vec1[:min_len]
            vec2 = vec2[:min_len]
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(np.dot(vec1, vec2) / (norm1 * norm2))


embedding_service = EmbeddingService()

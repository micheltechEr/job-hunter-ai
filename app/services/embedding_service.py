import hashlib
import logging
from typing import List, Dict
import numpy as np
from openai import AsyncOpenAI
from app.config import settings
from app.services.rate_limiter import execute_with_llm_protection

logger = logging.getLogger("job_hunter.embedding_service")


class EmbeddingService:
    def __init__(self):
        api_key = settings.LLM_API_KEY or "placeholder-key"
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.LLM_BASE_URL
        )
        self.model = "text-embedding-3-small"
        # In-memory vector cache to eliminate redundant embedding API calls and accelerate ATS
        self._cache: Dict[str, List[float]] = {}

    def _get_cache_key(self, text: str) -> str:
        """Computes SHA-256 hash of normalized text for fast cache lookup."""
        normalized = " ".join(text.strip().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    async def get_embedding(self, text: str) -> List[float]:
        """Fetches vector embedding with in-memory cache and rate limit protection."""
        if not text or not text.strip():
            return [0.0] * 1536

        cache_key = self._get_cache_key(text)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not settings.LLM_API_KEY:
            zero_vec = [0.0] * 1536
            self._cache[cache_key] = zero_vec
            return zero_vec

        async def _call_embedding(model_name: str):
            return await self.client.embeddings.create(
                input=[text[:8000]],  # bounded text limit to avoid token overflow
                model=model_name
            )

        try:
            response = await execute_with_llm_protection(_call_embedding, self.model)
            vec = response.data[0].embedding
            self._cache[cache_key] = vec
            return vec
        except Exception as e:
            logger.warning(f"Error fetching embedding with {self.model}: {e}. Retrying with text-embedding-ada-002.")
            try:
                response = await execute_with_llm_protection(_call_embedding, "text-embedding-ada-002")
                vec = response.data[0].embedding
                self._cache[cache_key] = vec
                return vec
            except Exception as ex:
                logger.error(f"All embedding models failed: {ex}")
                zero_vec = [0.0] * 1536
                return zero_vec

    @staticmethod
    def calculate_similarity(v1: List[float], v2: List[float]) -> float:
        """Computes cosine similarity between two vector lists."""
        if not v1 or not v2:
            return 0.0
        vec1 = np.array(v1, dtype=np.float32)
        vec2 = np.array(v2, dtype=np.float32)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(np.dot(vec1, vec2) / (norm1 * norm2))


embedding_service = EmbeddingService()

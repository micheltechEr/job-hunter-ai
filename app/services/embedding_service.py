import numpy as np
import logging
from typing import List
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

    async def get_embedding(self, text: str) -> List[float]:
        """Fetches vector embedding for a given text with rate limit and backoff protection."""
        if not settings.LLM_API_KEY:
            return [0.0] * 1536

        async def _call_embedding(model_name: str):
            return await self.client.embeddings.create(
                input=[text],
                model=model_name
            )

        try:
            response = await execute_with_llm_protection(_call_embedding, self.model)
            return response.data[0].embedding
        except Exception as e:
            logger.warning(f"Error fetching embedding with {self.model}: {e}. Retrying with text-embedding-ada-002.")
            try:
                response = await execute_with_llm_protection(_call_embedding, "text-embedding-ada-002")
                return response.data[0].embedding
            except Exception as ex:
                logger.error(f"All embedding models failed: {ex}")
                return [0.0] * 1536

    @staticmethod
    def cosine_similarity(v1: List[float], v2: List[float]) -> float:
        """Computes cosine similarity between two vector lists."""
        if not v1 or not v2:
            return 0.0
        vec1 = np.array(v1)
        vec2 = np.array(v2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(np.dot(vec1, vec2) / (norm1 * norm2))

    @classmethod
    def calculate_similarity(cls, v1: List[float], v2: List[float]) -> float:
        """Alias for cosine_similarity."""
        return cls.cosine_similarity(v1, v2)

embedding_service = EmbeddingService()

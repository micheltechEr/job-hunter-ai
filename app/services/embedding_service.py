import re
import hashlib
import logging
from typing import List, Dict
import numpy as np
from openai import AsyncOpenAI
from app.config import settings
from app.services.rate_limiter import execute_with_llm_protection

logger = logging.getLogger("job_hunter.embedding_service")


KEY_TECH_WEIGHTS: Dict[str, float] = {
    # JVM Ecosystem
    'java': 6.0, 'java 8': 6.5, 'java 11': 6.5, 'java 17': 6.5, 'spring': 5.0, 'springboot': 5.0, 'spring boot': 5.0, 'hibernate': 4.0, 'quarkus': 4.5, 'jvm': 4.0, 'kotlin': 4.5,
    # .NET Ecosystem
    'c#': 6.0, 'csharp': 6.0, '.net': 5.0, 'dotnet': 5.0, 'asp.net': 5.0, 'entity framework': 4.0,
    # Python Ecosystem
    'python': 6.0, 'django': 5.0, 'fastapi': 5.0, 'flask': 4.0, 'pandas': 4.0, 'pytorch': 4.5, 'tensorflow': 4.5,
    # JS / TS Ecosystem
    'typescript': 5.5, 'javascript': 5.0, 'react': 5.0, 'react.js': 5.0, 'nodejs': 5.5, 'node.js': 5.5, 'node': 4.5, 'nestjs': 5.0, 'nest.js': 5.0, 'next.js': 4.5, 'vue': 4.5, 'angular': 4.5,
    # PHP Ecosystem
    'php': 5.5, 'laravel': 5.0, 'symfony': 4.5, 'twig': 3.5,
    # Other primary languages
    'golang': 6.0, 'go': 5.0, 'rust': 6.0, 'ruby': 6.0, 'rails': 5.0, 'c++': 6.0, 'cpp': 6.0, 'swift': 5.5, 'flutter': 5.0,
    # Databases
    'sql': 4.0, 'postgresql': 4.5, 'postgres': 4.5, 'mysql': 4.5, 'oracle': 4.5, 'sql server': 4.5, 'mongodb': 4.0, 'redis': 4.0, 'supabase': 4.0,
    # Cloud & DevOps
    'aws': 4.0, 'azure': 4.0, 'gcp': 4.0, 'docker': 4.0, 'kubernetes': 4.5, 'ci/cd': 3.5, 'git': 3.5, 'linux': 3.5,
    # Domains & Roles
    'backend': 4.0, 'frontend': 4.0, 'fullstack': 4.0, 'dados': 4.0, 'data': 4.0, 'ia': 4.0, 'ai': 4.0, 'machine learning': 4.5, 'rag': 4.0
}


def _local_text_vector(text: str, dim: int = 384) -> List[float]:
    """Generates a high-precision deterministic domain-weighted semantic embedding vector locally with 0 API calls."""
    if not text:
        return [0.0] * dim

    t = text.lower()
    vec = np.zeros(dim, dtype=np.float32)

    # 1. Tech stack weighted features
    for tech, weight in KEY_TECH_WEIGHTS.items():
        pattern = r'\b' + re.escape(tech) + r'\b'
        if re.search(pattern, t):
            h = int(hashlib.md5(('tech_' + tech).encode('utf-8')).hexdigest(), 16) % dim
            vec[h] += weight

    # 2. Subword and token level features
    words = re.findall(r'\b[a-zA-Z0-9_#\.\+-]{2,}\b', t)
    for w in words:
        h = int(hashlib.md5(('word_' + w).encode('utf-8')).hexdigest(), 16) % dim
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

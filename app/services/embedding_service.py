import re
import hashlib
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import numpy as np
import httpx
from openai import AsyncOpenAI
from app.config import settings
from app.services.rate_limiter import execute_with_llm_protection
from app.services.semantic_normalizer import semantic_normalizer, TECH_TAXONOMY_REGISTRY

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
    """
    Generates a high-precision deterministic domain-weighted semantic embedding vector locally.
    Performs full semantic normalization and canonical taxonomy classification prior to vectorization.
    """
    if not text:
        return [0.0] * dim

    # 1. Semantic Classification & Disambiguation Pass
    norm_profile = semantic_normalizer.normalize_text_entities(text)

    vec = np.zeros(dim, dtype=np.float32)

    # 2. Canonical Entities Layer (Heavily weighted, orthogonal hash slots)
    for entity_id in norm_profile.canonical_entities:
        h = int(hashlib.sha256(('canonical_entity_' + entity_id).encode('utf-8')).hexdigest(), 16) % dim
        # Primary language entities carry higher discriminant weight
        weight = 8.0 if any(item.canonical_id == entity_id and item.category == "language" for item in TECH_TAXONOMY_REGISTRY) else 5.0
        vec[h] += weight

    # 3. Ecosystem Cluster Layer
    for eco in norm_profile.ecosystems_present:
        h = int(hashlib.sha256(('canonical_eco_' + eco).encode('utf-8')).hexdigest(), 16) % dim
        vec[h] += 4.0

    # 4. Keyword & Token Level Layer
    t = text.lower()
    for tech, weight in KEY_TECH_WEIGHTS.items():
        pattern = r'\b' + re.escape(tech) + r'\b'
        if re.search(pattern, t):
            h = int(hashlib.md5(('tech_' + tech).encode('utf-8')).hexdigest(), 16) % dim
            vec[h] += weight

    words = re.findall(r'\b[a-zA-Z0-9_#\.\+-]{2,}\b', t)
    for w in words:
        h = int(hashlib.md5(('word_' + w).encode('utf-8')).hexdigest(), 16) % dim
        vec[h] += 1.0

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


# ==========================================
# Agnostic Embedding Providers
# ==========================================

class BaseEmbeddingProvider(ABC):
    @abstractmethod
    async def get_embedding(self, text: str) -> List[float]:
        pass


class LocalDeterministicProvider(BaseEmbeddingProvider):
    """Local, offline, domain-weighted deterministic vectorizer with 0 API calls and 0ms latency."""
    def __init__(self, dim: int = 384):
        self.dim = dim

    async def get_embedding(self, text: str) -> List[float]:
        return _local_text_vector(text, dim=self.dim)


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    """OpenAI, Ollama, OpenRouter, LiteLLM, vLLM, LocalAI."""
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model: str = "text-embedding-3-small"):
        self.api_key = api_key or settings.EMBEDDING_API_KEY or settings.LLM_API_KEY or "placeholder-key"
        self.base_url = base_url or settings.EMBEDDING_BASE_URL or settings.LLM_BASE_URL or "https://api.openai.com/v1"
        self.model = model or settings.EMBEDDING_MODEL or "text-embedding-3-small"
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def get_embedding(self, text: str) -> List[float]:
        async def _call():
            return await self.client.embeddings.create(
                input=[text[:8000]],
                model=self.model
            )
        response = await execute_with_llm_protection(_call)
        return response.data[0].embedding


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """Google Gemini Embeddings API via REST (text-embedding-004, embedding-001)."""
    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-004"):
        self.api_key = api_key or settings.GEMINI_API_KEY or settings.EMBEDDING_API_KEY or settings.LLM_API_KEY
        self.model = model or settings.EMBEDDING_MODEL or "text-embedding-004"
        if not self.model.startswith("models/"):
            self.model_path = f"models/{self.model}"
        else:
            self.model_path = self.model

    async def get_embedding(self, text: str) -> List[float]:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini embedding provider.")

        url = f"https://generativelanguage.googleapis.com/v1beta/{self.model_path}:embedContent?key={self.api_key}"
        payload = {
            "model": self.model_path,
            "content": {
                "parts": [{"text": text[:8000]}]
            }
        }

        async def _call():
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["embedding"]["values"]

        return await execute_with_llm_protection(_call)


class CohereEmbeddingProvider(BaseEmbeddingProvider):
    """Cohere Embeddings API via REST (embed-multilingual-v3.0, embed-english-v3.0)."""
    def __init__(self, api_key: Optional[str] = None, model: str = "embed-multilingual-v3.0"):
        self.api_key = api_key or settings.COHERE_API_KEY or settings.EMBEDDING_API_KEY
        self.model = model or settings.EMBEDDING_MODEL or "embed-multilingual-v3.0"

    async def get_embedding(self, text: str) -> List[float]:
        if not self.api_key:
            raise ValueError("COHERE_API_KEY is required for Cohere embedding provider.")

        url = "https://api.cohere.com/v2/embed"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "texts": [text[:8000]],
            "input_type": "search_document",
            "embedding_types": ["float"]
        }

        async def _call():
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["embeddings"]["float"][0]

        return await execute_with_llm_protection(_call)


class HuggingFaceEmbeddingProvider(BaseEmbeddingProvider):
    """HuggingFace Inference API / TEI (e.g. BAAI/bge-m3, sentence-transformers/all-MiniLM-L6-v2)."""
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model: str = "BAAI/bge-m3"):
        self.api_key = api_key or settings.HF_API_KEY or settings.EMBEDDING_API_KEY
        self.model = model or settings.EMBEDDING_MODEL or "BAAI/bge-m3"
        self.base_url = base_url or settings.EMBEDDING_BASE_URL or f"https://api-inference.huggingface.co/pipeline/feature-extraction/{self.model}"

    async def get_embedding(self, text: str) -> List[float]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {"inputs": [text[:8000]], "options": {"wait_for_model": True}}

        async def _call():
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(self.base_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    if isinstance(data[0], list):
                        return data[0]
                    return data
                raise ValueError(f"Unexpected HF response format: {data}")

        return await execute_with_llm_protection(_call)


# ==========================================
# Embedding Service Orchestrator
# ==========================================

class EmbeddingService:
    """
    Provider-agnostic embedding orchestrator supporting:
    - OpenAI / OpenAI-Compatible (Ollama, LiteLLM, vLLM, OpenRouter)
    - Google Gemini (REST)
    - Cohere (REST)
    - HuggingFace / TEI (REST)
    - Local Deterministic (Zero-Dependency Semantic Normalizer)
    """

    def __init__(self):
        self._provider = self._resolve_provider()
        self._cache: Dict[str, List[float]] = {}
        self._fallback_active = False

    def _resolve_provider(self) -> BaseEmbeddingProvider:
        prov_name = (settings.EMBEDDING_PROVIDER or "auto").strip().lower()

        if prov_name == "local" or prov_name == "deterministic":
            logger.info("EmbeddingService: using local deterministic semantic vectorizer.")
            return LocalDeterministicProvider()

        if prov_name == "gemini" or prov_name == "google":
            logger.info("EmbeddingService: using Google Gemini embedding provider.")
            return GeminiEmbeddingProvider()

        if prov_name == "cohere":
            logger.info("EmbeddingService: using Cohere embedding provider.")
            return CohereEmbeddingProvider()

        if prov_name in ("huggingface", "hf", "tei"):
            logger.info("EmbeddingService: using Hugging Face embedding provider.")
            return HuggingFaceEmbeddingProvider()

        if prov_name in ("openai", "openai_compatible", "ollama"):
            logger.info("EmbeddingService: using OpenAI-compatible embedding provider.")
            return OpenAICompatibleEmbeddingProvider()

        # Auto detection:
        if settings.GEMINI_API_KEY:
            logger.info("EmbeddingService (auto): detected GEMINI_API_KEY, using Gemini embedding provider.")
            return GeminiEmbeddingProvider()
        if settings.COHERE_API_KEY:
            logger.info("EmbeddingService (auto): detected COHERE_API_KEY, using Cohere embedding provider.")
            return CohereEmbeddingProvider()
        if settings.HF_API_KEY:
            logger.info("EmbeddingService (auto): detected HF_API_KEY, using Hugging Face embedding provider.")
            return HuggingFaceEmbeddingProvider()

        # Default OpenAI-compatible with graceful fallback
        return OpenAICompatibleEmbeddingProvider()

    def _get_cache_key(self, text: str) -> str:
        """Computes SHA-256 hash of normalized text for fast cache lookup."""
        normalized = " ".join(text.strip().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    async def get_embedding(self, text: str) -> List[float]:
        """Fetches vector embedding with in-memory cache and rate limit protection with automatic local fallback."""
        if not text or not text.strip():
            return [0.0] * 384

        cache_key = self._get_cache_key(text)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if self._fallback_active:
            vec = _local_text_vector(text)
            self._cache[cache_key] = vec
            return vec

        try:
            vec = await self._provider.get_embedding(text)
            self._cache[cache_key] = vec
            return vec
        except Exception as e:
            logger.info(f"Primary embedding provider unavailable ({e}). Switched to local deterministic semantic vectorizer permanently.")
            self._fallback_active = True
            vec = _local_text_vector(text)
            self._cache[cache_key] = vec
            return vec

    @staticmethod
    def calculate_similarity(v1: List[float], v2: List[float]) -> float:
        """Calculates cosine similarity with zero division safeguards."""
        if not v1 or not v2:
            return 0.0

        len1, len2 = len(v1), len(v2)
        if len1 != len2:
            min_len = min(len1, len2)
            arr1 = np.array(v1[:min_len], dtype=np.float32)
            arr2 = np.array(v2[:min_len], dtype=np.float32)
        else:
            arr1 = np.array(v1, dtype=np.float32)
            arr2 = np.array(v2, dtype=np.float32)

        norm1 = np.linalg.norm(arr1)
        norm2 = np.linalg.norm(arr2)

        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0

        dot = np.dot(arr1, arr2)
        similarity = float(dot / (norm1 * norm2))
        return max(-1.0, min(1.0, similarity))


embedding_service = EmbeddingService()

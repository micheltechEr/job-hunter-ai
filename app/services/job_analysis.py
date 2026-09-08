import hashlib
import logging
from typing import Dict
from app.schemas.schemas import JobAnalysisResponse
from app.services.llm_service import llm_service

logger = logging.getLogger("job_hunter.job_analysis")


class JobAnalysisService:
    def __init__(self):
        # In-memory cache for parsed job descriptions to eliminate duplicate LLM calls
        self._cache: Dict[str, JobAnalysisResponse] = {}

    def _get_cache_key(self, description: str) -> str:
        normalized = " ".join(description.strip().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    async def analyze_job_description(self, description: str) -> JobAnalysisResponse:
        """Extracts key role criteria from a job description text with caching."""
        if not description or not description.strip():
            return JobAnalysisResponse(
                extracted_role="Não especificado",
                seniority="N/A",
                location="Remoto",
                work_mode="Remote",
                required_skills=[],
                nice_to_have=[],
                responsibilities=[],
                education=[],
                languages=[],
                experience_required=None
            )

        cache_key = self._get_cache_key(description)
        if cache_key in self._cache:
            return self._cache[cache_key]

        system_prompt = (
            "Você é um analisador técnico de vagas de TI. "
            "Extraia os dados estruturados em português de forma concisa e direta:"
            "- extracted_role (cargo), seniority (Junior/Pleno/Senior/N/A), location, work_mode (Remote/Hybrid/On-site), "
            "required_skills (lista de hard skills obrigatórias), nice_to_have, responsibilities, education, languages, experience_required."
        )
        user_prompt = f"Descrição da vaga:\n{description[:6000]}"

        analysis_obj = await llm_service.get_structured_output(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=JobAnalysisResponse
        )

        self._cache[cache_key] = analysis_obj
        return analysis_obj


job_analysis_service = JobAnalysisService()

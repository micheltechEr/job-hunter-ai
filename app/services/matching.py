import json
import hashlib
import logging
from typing import List, Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.models.db_models import Resume, UserProfile, Job, JobAnalysis
from app.schemas.schemas import MatchResponse
from app.services.embedding_service import embedding_service
from app.services.llm_service import llm_service

logger = logging.getLogger("job_hunter.matching")


class MatchingService:
    def __init__(self):
        # Cache to prevent duplicate ATS LLM matching calls for the same job and CV
        self._match_cache: Dict[str, MatchResponse] = {}

    def _get_cache_key(self, job_id: int, job_text: str, resume_id: Optional[int], profile_hash: str) -> str:
        raw = f"{job_id}:{job_text}:{resume_id}:{profile_hash}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def match_job_profile(self, db: AsyncSession, job_id: int) -> MatchResponse:
        """Determines compatibility score and fit category applying rigorous ATS methodology with caching & optimizations."""
        
        # 1. Fetch Job and its Analysis
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalars().first()
        if not job:
            raise ValueError(f"Job with id {job_id} not found.")

        result_analysis = await db.execute(select(JobAnalysis).where(JobAnalysis.job_id == job_id))
        job_analysis = result_analysis.scalars().first()
        if not job_analysis:
            raise ValueError(f"Analysis for job id {job_id} not found.")

        # 2. Fetch all Resumes
        result_resumes = await db.execute(select(Resume))
        resumes = result_resumes.scalars().all()
        if not resumes:
            raise ValueError("No resumes found in the database. Please upload at least one resume.")

        # 3. Fetch User Profile
        result_profile = await db.execute(
            select(UserProfile)
            .options(selectinload(UserProfile.experiences), selectinload(UserProfile.projects))
            .limit(1)
        )
        user_profile = result_profile.scalars().first()
        if not user_profile:
            raise ValueError("User profile not found. Please populate user profile details.")

        # 4. Rank resumes using embedding semantic similarity (with vector caching)
        job_text_for_embed = f"{job.title} | {job.company} | {job.description[:2000]}"
        job_embedding = await embedding_service.get_embedding(job_text_for_embed)

        best_resume: Optional[Resume] = None
        best_similarity = -1.0

        for res in resumes:
            if not res.embedding:
                cv_text = json.dumps(res.parsed_data or {})
                res.embedding = await embedding_service.get_embedding(cv_text)
                db.add(res)
                await db.commit()

            similarity = embedding_service.calculate_similarity(job_embedding, res.embedding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_resume = res

        # Check in-memory match cache
        profile_sig = f"{user_profile.name}:{len(user_profile.experiences)}:{user_profile.technologies}"
        cache_key = self._get_cache_key(
            job_id,
            job_analysis.extracted_role or job.title,
            best_resume.id if best_resume else None,
            profile_sig
        )
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]

        # 5. Fast keyword & hard skills intersection
        user_techs = set([t.lower().strip() for t in (user_profile.technologies or [])])
        for exp in user_profile.experiences:
            for sk in (exp.skills_used or []):
                user_techs.add(sk.lower().strip())
        
        job_req_skills = [s.strip() for s in (job_analysis.required_skills or [])]
        matched_quick = [s for s in job_req_skills if any(u in s.lower() or s.lower() in u for u in user_techs)]

        # 6. Call LLM applying strict ATS methodology (streamlined prompt)
        system_prompt = (
            "Você é um motor de triagem ATS (Applicant Tracking System) de TI altamente analítico e rigoroso.\n"
            "Avalie o candidato contra a vaga com a ponderação oficial:\n"
            "1. Hard Skills Obrigatórias (40%): Casamento de stack técnica.\n"
            "2. Senioridade & Experiência (25%): Anos e nível de senioridade.\n"
            "3. Responsabilidades & Domínio (15%): Entregas e arquitetura.\n"
            "4. Localização & Modalidade (10%): Remoto/Híbrido/Presencial.\n"
            "5. Diferenciais (10%): Nice to have e diferenciais.\n\n"
            "Critérios de Classificação:\n"
            "- >= 80%: HIGH_MATCH (recommendation=True)\n"
            "- 60 a 79%: GOOD_MATCH (recommendation=False)\n"
            "- 40 a 59%: REVIEW (recommendation=False)\n"
            "- < 40%: IGNORE (recommendation=False)\n"
            "Retorne score (0-100), fit, matched_requirements, missing_requirements, strengths, risks, recommendation, explanation (2 frases)."
        )

        user_prompt = f"""
CANDIDATO:
Nome: {user_profile.name}
Senioridade/Objetivo: {user_profile.professional_goals or 'Desenvolvedor'}
Stack: {', '.join(user_profile.technologies or [])}
Bancos: {', '.join(user_profile.databases or [])} | DevOps: {', '.join(user_profile.devops_tools or [])} | Cloud: {', '.join(user_profile.cloud_providers or [])}
Experiências Recentes: {'; '.join([f"{e.role} na {e.company} ({e.start_date or ''} - {e.end_date or 'Atual'})" for e in user_profile.experiences[:4]])}

VAGA DE EMPREGO:
Cargo: {job_analysis.extracted_role or job.title} | Empresa: {job.company} | Modalidade: {job_analysis.work_mode or job.work_mode}
Senioridade Exigida: {job_analysis.seniority or 'Não informada'} | Experiência: {job_analysis.experience_required or 'N/A'}
Requisitos Obrigatórios: {', '.join(job_analysis.required_skills or [])}
Diferenciais: {', '.join(job_analysis.nice_to_have or [])}
Skills já pré-identificadas no candidato: {', '.join(matched_quick) if matched_quick else 'Nenhuma óbvia'}
"""

        try:
            match_data = await llm_service.get_structured_output(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=MatchResponse
            )
            
            if best_resume:
                match_data.recommended_resume_id = best_resume.id
                match_data.recommended_resume_name = best_resume.version_name

            self._match_cache[cache_key] = match_data
            return match_data

        except Exception as e:
            logger.error(f"Error computing ATS match for job {job_id}: {e}")
            fallback_score = 50 if len(matched_quick) > 0 else 20
            return MatchResponse(
                score=fallback_score,
                fit="REVIEW" if fallback_score >= 50 else "IGNORE",
                matched_requirements=matched_quick,
                missing_requirements=["Análise automática indisponível"],
                strengths=["Perfil registrado"],
                risks=["Verificar manualmente requisitos da vaga"],
                recommendation=False,
                explanation="Score preliminar gerado por cruzamento heurístico de hard skills.",
                recommended_resume_id=best_resume.id if best_resume else None,
                recommended_resume_name=best_resume.version_name if best_resume else None
            )


matching_service = MatchingService()

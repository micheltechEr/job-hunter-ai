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
from app.services.scraper_service import is_senior_title

logger = logging.getLogger("job_hunter.matching")


PRIMARY_TECH_ECOSYSTEMS = [
    ("Java / JVM", [r"\bjava\s*8\b", r"\bjava\s*11\b", r"\bjava\s*17\b", r"\bjava\b", r"\bspring\s*boot\b", r"\bspring\b", r"\bhibernate\b", r"\bquarkus\b", r"\bjvm\b", r"\bkotlin\b"]),
    ("C# / .NET", [r"\bc#\b", r"\bcsharp\b", r"\b\.net\b", r"\bdotnet\b", r"\basp\.net\b", r"\bentity\s+framework\b"]),
    ("Python", [r"\bpython\b", r"\bdjango\b", r"\bfastapi\b", r"\bflask\b"]),
    ("Golang", [r"\bgolang\b", r"\bgo\b"]),
    ("Rust", [r"\brust\b"]),
    ("Ruby / Rails", [r"\bruby\b", r"\brails\b", r"\bruby\s+on\s+rails\b"]),
    ("PHP", [r"\bphp\b", r"\blaravel\b", r"\bsymfony\b"]),
    ("Mobile Nativo", [r"\bflutter\b", r"\bswift\b", r"\bios\b", r"\bandroid\s+nativo\b"])
]


def detect_missing_primary_stack(job_text: str, candidate_text: str) -> List[str]:
    """Detects if job mandates a primary language ecosystem completely absent in candidate profile."""
    import re
    j_lower = f" {job_text.lower()} "
    c_lower = f" {candidate_text.lower()} "
    missing = []

    for eco_name, patterns in PRIMARY_TECH_ECOSYSTEMS:
        job_has_eco = any(re.search(pat, j_lower) for pat in patterns)
        if job_has_eco:
            cand_has_eco = any(re.search(pat, c_lower) for pat in patterns)
            if not cand_has_eco:
                missing.append(eco_name)
    return missing


class MatchingService:
    def __init__(self):
        # Cache to prevent duplicate ATS LLM matching calls for the same job and CV
        self._match_cache: Dict[str, MatchResponse] = {}

    def _get_cache_key(self, job_id: int, job_text: str, resume_id: Optional[int], profile_hash: str) -> str:
        raw = f"{job_id}:{job_text}:{resume_id}:{profile_hash}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def match_job_profile(
        self,
        db: AsyncSession,
        job_id: int,
        job: Optional[Job] = None,
        job_analysis: Optional[JobAnalysis] = None,
        user_profile: Optional[UserProfile] = None,
        resumes: Optional[List[Resume]] = None
    ) -> MatchResponse:
        """Determines compatibility score and fit category applying rigorous ATS methodology with caching & optimizations."""
        
        # 1. Fetch Job and its Analysis if not provided
        if not job:
            result = await db.execute(select(Job).where(Job.id == job_id))
            job = result.scalars().first()
            if not job:
                raise ValueError(f"Job with id {job_id} not found.")

        if not job_analysis:
            result_analysis = await db.execute(select(JobAnalysis).where(JobAnalysis.job_id == job_id))
            job_analysis = result_analysis.scalars().first()
            if not job_analysis:
                from app.services.job_analysis import job_analysis_service
                analysis_data = await job_analysis_service.analyze_job_description(job.description or job.title)
                job_analysis = JobAnalysis(
                    job_id=job.id,
                    extracted_role=analysis_data.extracted_role,
                    seniority=analysis_data.seniority,
                    location=analysis_data.location,
                    work_mode=analysis_data.work_mode,
                    required_skills=analysis_data.required_skills,
                    nice_to_have=analysis_data.nice_to_have,
                    responsibilities=analysis_data.responsibilities,
                    education=analysis_data.education,
                    languages=analysis_data.languages,
                    experience_required=analysis_data.experience_required,
                    raw_json=analysis_data.model_dump() if hasattr(analysis_data, "model_dump") else analysis_data.dict()
                )
                db.add(job_analysis)
                await db.commit()
                await db.refresh(job_analysis)

        # 2. Fetch all Resumes if not provided
        if resumes is None:
            result_resumes = await db.execute(select(Resume))
            resumes = result_resumes.scalars().all()

        # 3. Fetch User Profile if not provided
        if not user_profile:
            result_profile = await db.execute(
                select(UserProfile)
                .options(selectinload(UserProfile.experiences), selectinload(UserProfile.projects))
                .limit(1)
            )
            user_profile = result_profile.scalars().first()

        # Graceful fallback if no resume or profile is registered yet
        if not resumes or not user_profile:
            return MatchResponse(
                score=50,
                fit="REVIEW",
                matched_requirements=[],
                missing_requirements=["Currículo ou perfil ainda não cadastrado"],
                strengths=["Vaga registrada"],
                risks=["Carregue um currículo para score ATS personalizado"],
                recommendation=False,
                explanation="Aguardando upload de currículo para cálculo ATS personalizado.",
                recommended_resume_id=None,
                recommended_resume_name=None
            )

        profile_sig = f"{user_profile.name}:{len(user_profile.experiences)}:{user_profile.technologies}"
        role_key = job_analysis.extracted_role or job.title

        # Fast cache check by job_id + role + profile before any embedding computation
        quick_cache_key = f"{job_id}:{role_key}:{profile_sig}"
        if quick_cache_key in self._match_cache:
            logger.info(f"Fast Cache HIT for Job Match {job_id} ({job.title})")
            return self._match_cache[quick_cache_key]

        # 4. Rank resumes (Fast path for single resume vs multi-resume semantic embedding)
        best_resume: Optional[Resume] = None
        if len(resumes) == 1:
            best_resume = resumes[0]
        else:
            job_text_for_embed = f"{job.title} | {job.company} | {job.description[:2000]}"
            job_embedding = await embedding_service.get_embedding(job_text_for_embed)
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

        # Check precise cache key
        cache_key = self._get_cache_key(
            job_id,
            role_key,
            best_resume.id if best_resume else None,
            profile_sig
        )
        if cache_key in self._match_cache:
            logger.info(f"Precise Cache HIT for Job Match {job_id}")
            return self._match_cache[cache_key]

        # 5. Fast keyword & hard skills intersection
        user_techs = set([t.lower().strip() for t in (user_profile.technologies or [])])
        for exp in user_profile.experiences:
            for sk in (exp.skills_used or []):
                user_techs.add(sk.lower().strip())
        
        job_req_skills = [s.strip() for s in (job_analysis.required_skills or [])]
        matched_quick = [s for s in job_req_skills if any(u in s.lower() or s.lower() in u for u in user_techs)]

        # Check primary language ecosystem compatibility
        job_full_content = f"{job.title} {job.description or ''} {' '.join(job_analysis.required_skills or [])}"
        exp_parts = []
        for e in (user_profile.experiences or []):
            role_str = e.role or ""
            desc_str = e.description or ""
            skills_str = " ".join(e.skills_used or [])
            exp_parts.append(f"{role_str} {desc_str} {skills_str}")
        cand_full_content = f"{' '.join(user_profile.technologies or [])} {' '.join(user_profile.databases or [])} {' '.join(user_profile.devops_tools or [])} {' '.join(exp_parts)}"
        
        missing_stacks = detect_missing_primary_stack(job_full_content, cand_full_content)

        # 6. Call LLM applying strict ATS methodology (streamlined prompt)
        candidate_seniority = user_profile.seniority_level or "Junior"
        candidate_exp_years = user_profile.years_of_experience or 0.0
        job_is_senior = (job_analysis.seniority and "senior" in job_analysis.seniority.lower()) or is_senior_title(job.title)

        system_prompt = (
            "Você é um motor de triagem ATS (Applicant Tracking System) de TI altamente analítico e rigoroso.\n"
            "Avalie o candidato contra a vaga com a ponderação oficial:\n"
            "1. Hard Skills Obrigatórias (40%): Casamento de stack técnica.\n"
            "2. Senioridade & Experiência (25%): Anos e nível de senioridade.\n"
            "3. Responsabilidades & Domínio (15%): Entregas e arquitetura.\n"
            "4. Localização & Modalidade (10%): Remoto/Híbrido/Presencial.\n"
            "5. Diferenciais (10%): Nice to have e diferenciais.\n\n"
            "REGRAS DE GATING DE HARD SKILLS E SENIORIDADE:\n"
            "- Se a vaga exigir uma linguagem/stack primária obrigatória (ex: Java, C#, Python, Golang, Ruby) que NÃO conste no perfil do candidato, DESQUALIFIQUE: pontuação máxima de 35%, fit='IGNORE', recommendation=False e declare o gap técnico nos risks.\n"
            "- Se a vaga for Sênior/Lead/Staff e o candidato for Júnior ou Pleno com menos de 4 anos de experiência, DESQUALIFIQUE: pontuação máxima de 35%, fit='IGNORE', recommendation=False e declare o gap de senioridade nos risks.\n"
            "- Vagas compatíveis com a senioridade real do candidato (Júnior/Pleno) e com stack alinhada devem ser avaliadas normalmente.\n\n"
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
Senioridade Real: {candidate_seniority} (~{candidate_exp_years} anos de experiência)
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

            # Deterministic Seniority Guard: Enforce penalty if Junior/Pleno candidate vs Senior job
            if job_is_senior and candidate_seniority.lower() in ["junior", "estagio", "estagiario"]:
                if match_data.score > 35:
                    match_data.score = 35
                match_data.fit = "IGNORE"
                match_data.recommendation = False
                seniority_risk = "Incompatibilidade: vaga exige nível Sênior/Especialista."
                if seniority_risk not in match_data.risks:
                    match_data.risks.append(seniority_risk)

            # Deterministic Primary Stack Guard: Enforce disqualification if required language ecosystem is absent
            if missing_stacks:
                if match_data.score > 35:
                    match_data.score = 35
                match_data.fit = "IGNORE"
                match_data.recommendation = False
                for s in missing_stacks:
                    stack_risk = f"Incompatibilidade crítica de stack: Vaga exige {s}, ausente no histórico técnico do candidato."
                    if stack_risk not in match_data.risks:
                        match_data.risks.append(stack_risk)
                    if s not in match_data.missing_requirements:
                        match_data.missing_requirements.append(s)

            if best_resume:
                match_data.recommended_resume_id = best_resume.id
                match_data.recommended_resume_name = best_resume.version_name

            self._match_cache[cache_key] = match_data
            self._match_cache[quick_cache_key] = match_data
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

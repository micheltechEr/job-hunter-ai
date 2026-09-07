import json
import logging
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.models.db_models import Resume, UserProfile, Job, JobAnalysis
from app.schemas.schemas import MatchResponse
from app.services.embedding_service import embedding_service
from app.services.llm_service import llm_service

logger = logging.getLogger("job_hunter.matching")

class MatchingService:
    async def match_job_profile(self, db: AsyncSession, job_id: int) -> MatchResponse:
        """Determines compatibility score and fit category applying rigorous ATS (Applicant Tracking System) methodology."""
        
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
        result_profile = await db.execute(select(UserProfile).options(selectinload(UserProfile.experiences), selectinload(UserProfile.projects)).limit(1))
        user_profile = result_profile.scalars().first()
        if not user_profile:
            raise ValueError("User profile not found. Please populate user profile first.")

        # 4. Rank resumes using embedding semantic similarity
        job_text_for_embed = f"{job.title} | {job.company} | {job.description}"
        job_embedding = await embedding_service.get_embedding(job_text_for_embed)

        best_resume: Optional[Resume] = None
        best_similarity = -1.0

        for res in resumes:
            if not res.embedding:
                cv_text = json.dumps(res.parsed_data)
                res.embedding = await embedding_service.get_embedding(cv_text)
                db.add(res)
                await db.commit()

            similarity = embedding_service.calculate_similarity(job_embedding, res.embedding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_resume = res

        # 5. Call LLM applying strict ATS (Applicant Tracking System) methodology
        system_prompt = (
            "Você é um motor de triagem ATS (Applicant Tracking System) altamente analítico e rigoroso.\n"
            "Sua função é avaliar o perfil do candidato contra os requisitos da vaga utilizando a metodologia ATS com a seguinte ponderação oficial:\n"
            "1. Hard Skills & Stack Técnica Obrigatória (Peso 40%): Casamento de linguagens, frameworks, bancos de dados e ferramentas essenciais exigidas.\n"
            "2. Senioridade & Tempo de Experiência (Peso 25%): Compatibilidade entre a senioridade exigida (Júnior, Pleno, Sênior, Especialista, Lead) e o histórico profissional do candidato.\n"
            "3. Responsabilidades & Domínio Arquitetural (Peso 15%): Vivência prática e alinhamento com as responsabilidades e entregas descritas na vaga.\n"
            "4. Localização, Modalidade & Idiomas (Peso 10%): Aderência à modalidade de trabalho (Remoto/Híbrido/Presencial) e requisitos de idioma.\n"
            "5. Diferenciais & Nice to Have (Peso 10%): Tecnologias complementares, metodologias ágeis e certificações relevantes.\n\n"
            "CRITÉRIO DE CORTE ATS:\n"
            "- Score >= 80%: APROVADO NO ATS (HIGH_MATCH) - Alta probabilidade de aprovação direta no filtro dos recrutadores.\n"
            "- Score 60-79%: EM ANÁLISE / GAPS MENORES (GOOD_MATCH) - Possui base técnica porém com faltas secundárias.\n"
            "- Score 40-59%: REVISÃO CRÍTICA (REVIEW) - Gaps expressivos em hard skills ou senioridade.\n"
            "- Score < 40%: DESCARTE PELO ATS (IGNORE) - Falta de requisitos obrigatórios eliminatórios.\n\n"
            "Penalize severamente notas quando houver ausência de requisitos eliminatórios ou incompatibilidade de modalidade presencial distante.\n"
            "Retorne a análise estritamente em formato JSON seguindo o schema fornecido."
        )
        
        profile_summary = f"""
Nome: {user_profile.name}
Objetivos: {user_profile.professional_goals}
Modalidade Desejada: {user_profile.work_mode}
Localização: {user_profile.location}
Cargos de Interesse: {user_profile.desired_roles}
Tecnologias principais: {user_profile.technologies}
Cloud Providers: {user_profile.cloud_providers}
Ferramentas DevOps: {user_profile.devops_tools}
Bancos de Dados: {user_profile.databases}
Certificações: {user_profile.certifications}
Idiomas: {user_profile.languages}
        """

        job_summary = f"""
Título da Vaga: {job.title}
Empresa: {job.company}
Requisitos Obrigatórios: {job_analysis.required_skills}
Desejáveis/Diferenciais: {job_analysis.nice_to_have}
Responsabilidades: {job_analysis.responsibilities}
Senioridade: {job_analysis.seniority}
Localização e Modalidade: {job_analysis.location} ({job_analysis.work_mode})
Anos de Experiência: {job_analysis.experience_required}
        """

        user_prompt = f"""
Avalie este candidato para a vaga aplicando rigorosamente a metodologia ATS:

PERFIL DO CANDIDATO:
{profile_summary}

VAGA DE EMPREGO:
{job_summary}

CURRÍCULO SELECIONADO:
Nome da Versão: {best_resume.version_name if best_resume else 'Nenhuma'}
Detalhes: {json.dumps(best_resume.parsed_data) if best_resume else 'Não disponível'}

Instruções para o retorno JSON:
1. 'score': Inteiro de 0 a 100 calculado pela média ponderada dos critérios ATS. Vagas compatíveis com alta probabilidade de aceite devem receber >= 80.
2. 'fit': Classificação ATS:
   * >= 80: 'HIGH_MATCH'
   * 60 a 79: 'GOOD_MATCH'
   * 40 a 59: 'REVIEW'
   * < 40: 'IGNORE'
3. 'matched_requirements': Lista de requisitos técnicos e comportamentais atendidos com base no perfil.
4. 'missing_requirements': Lista de requisitos técnicos não atendidos (gaps).
5. 'strengths': Diferenciais do candidato que potencializam a aprovação no ATS.
6. 'risks': Pontos de atenção ou riscos detectados pelo filtro ATS.
7. 'recommendation': booleano (True se score >= 80, indicando que o candidato passa no crivo ATS com alta probabilidade de aceite; False caso contrário).
8. 'explanation': Justificativa clara e técnica do score ATS em 2 a 3 frases.
"""

        try:
            match_data = await llm_service.get_structured_output(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=MatchResponse
            )
            
            # Map selected CV values back
            if best_resume:
                match_data.recommended_resume_id = best_resume.id
                match_data.recommended_resume_name = best_resume.version_name
            
            # Enforce ATS threshold consistency
            if match_data.score >= 80:
                match_data.fit = "HIGH_MATCH"
                match_data.recommendation = True
            elif match_data.score >= 60:
                match_data.fit = "GOOD_MATCH"
                match_data.recommendation = False
            elif match_data.score >= 40:
                match_data.fit = "REVIEW"
                match_data.recommendation = False
            else:
                match_data.fit = "IGNORE"
                match_data.recommendation = False

            return match_data
            
        except Exception as e:
            logger.error(f"Error in deep ATS matching calculation: {e}")
            raise RuntimeError(f"Erro ao processar matching ATS da vaga: {str(e)}")

matching_service = MatchingService()

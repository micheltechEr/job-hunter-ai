import json
import logging
from app.models.db_models import Job, JobAnalysis
from app.schemas.schemas import JobAnalysisResponse
from app.services.llm_service import llm_service
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("job_hunter.job_analysis")

class JobAnalysisService:
    async def analyze_job_description(self, description: str) -> JobAnalysisResponse:
        """Calls the LLM to extract key role criteria from a job description text."""
        system_prompt = (
            "Você é um recrutador técnico experiente em TI.\n"
            "Analise a descrição da vaga fornecida e extraia as seguintes informações estruturadas em português:\n"
            "- extracted_role: Cargo ou especialidade (ex: DevOps Engineer, Backend Django, etc.)\n"
            "- seniority: Nível de senioridade (ex: Junior, Pleno, Senior, ou N/A)\n"
            "- location: Cidade, Estado, País ou se é totalmente Remoto\n"
            "- work_mode: Modalidade (Remote, Hybrid, On-site)\n"
            "- required_skills: Lista exata dos requisitos técnicos essenciais / obrigatórios (ex: Python, Docker, Git)\n"
            "- nice_to_have: Desejáveis ou diferenciais (ex: Kubernetes, Terraform, AWS)\n"
            "- responsibilities: Atividades principais descritas na oportunidade\n"
            "- education: Nível acadêmico exigido se citado (ex: Bacharelado em TI)\n"
            "- languages: Idiomas obrigatórios ou diferenciais (ex: Inglês Avançado)\n"
            "- experience_required: Anos de experiência exigidos ou tempo mínimo citado\n\n"
            "Seja objetivo. Se alguma informação não estiver declarada, retorne lista vazia ou nulo."
        )
        user_prompt = f"Aqui está a descrição completa da vaga:\n\n{description}"

        analysis_obj = await llm_service.get_structured_output(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=JobAnalysisResponse
        )
        return analysis_obj

    async def save_job_analysis(self, db: AsyncSession, job_id: int, analysis_data: JobAnalysisResponse) -> JobAnalysis:
        """Saves the extracted job analysis info in the database."""
        db_analysis = JobAnalysis(
            job_id=job_id,
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
            raw_json=analysis_data.model_dump()
        )
        db.add(db_analysis)
        await db.commit()
        await db.refresh(db_analysis)
        return db_analysis

job_analysis_service = JobAnalysisService()

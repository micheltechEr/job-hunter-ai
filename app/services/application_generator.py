import json
import hashlib
import logging
from pydantic import BaseModel, Field
from typing import Optional, Dict
from app.models.db_models import UserProfile, Job, JobAnalysis
from app.services.llm_service import llm_service

logger = logging.getLogger("job_hunter.application_generator")

class ApplicationDraftSchema(BaseModel):
    subject: str = Field(description="Assunto formal do e-mail de candidatura")
    body: str = Field(description="Corpo do e-mail de apresentação curto, natural e profissional")
    recipient_email: Optional[str] = Field(None, description="E-mail de destino do recrutador se estiver contido na vaga")


class ApplicationGeneratorService:
    def __init__(self):
        # In-memory cache to prevent duplicate draft generation for the same job and user profile
        self._draft_cache: Dict[str, ApplicationDraftSchema] = {}

    def _get_cache_key(self, user_profile: UserProfile, job: Job) -> str:
        job_id = getattr(job, "id", None) or "noid"
        job_desc_hash = hashlib.sha256((job.description or "").encode("utf-8")).hexdigest()[:16]
        profile_sig = f"{user_profile.name}:{len(user_profile.experiences or [])}:{user_profile.technologies}:{user_profile.professional_goals}"
        profile_hash = hashlib.sha256(profile_sig.encode("utf-8")).hexdigest()[:16]
        return f"{job_id}:{job_desc_hash}:{profile_hash}"

    async def generate_draft(self, user_profile: UserProfile, job: Job, job_class: JobAnalysis) -> ApplicationDraftSchema:
        """Call LLM to write an aligned, ultra-personalized profile application email for the job with memoized caching."""
        cache_key = self._get_cache_key(user_profile, job)
        if cache_key in self._draft_cache:
            logger.info(f"Cache HIT for Application Draft on job {job.title}")
            return self._draft_cache[cache_key]

        system_prompt = (
            "Você é um redator profissional sênior especializado em comunicação corporativa e contratação de TI no Brasil.\n"
            "Seu papel é criar o assunto e o corpo de um e-mail de apresentação para candidatura a uma vaga.\n"
            "Diretrizes:\n"
            "1. O texto deve ser curto (máximo de 3 parágrafos), natural, cordial e direto.\n"
            "2. Não invente NENHUMA habilidade, cargo ou experiência que não esteja no perfil do profissional.\n"
            "3. Enfatize um ou dois pontos fortes reais do profissional que batem com as necessidades da vaga.\n"
            "4. Evite jargões de spam ou robotizados (ex: 'Prezados Senhores', 'Venho por meio desta apresentar meu pleito'). Escreva de forma humana.\n"
            "5. Se encontrar algum e-mail de contato na descrição da vaga ou URL, extraia-o para o campo 'recipient_email'."
        )

        profile_text = f"""
Nome do Candidato: {user_profile.name}
Formação: {user_profile.education}
Localização: {user_profile.location}
Objetivos: {user_profile.professional_goals}
Modalidades Aceitas: {user_profile.work_mode}
Tecnologias dominadas: {user_profile.technologies}
Cloud Providers: {user_profile.cloud_providers}
Ferramentas DevOps: {user_profile.devops_tools}
Bancos de dados: {user_profile.databases}
Certificações: {user_profile.certifications}
        """

        job_text = f"""
Título da Vaga: {job.title}
Empresa: {job.company}
Requisitos Essenciais: {job_class.required_skills}
Desejáveis: {job_class.nice_to_have}
Descritivo Completo da Vaga:
{job.description}
        """

        user_prompt = f"""
Aqui estão as informações para basear a escrita:

PERFIL DO CANDIDATO:
{profile_text}

VAGA DE EMPREGO:
{job_text}

Crie a mensagem de candidatura. Lembre-se, use somente fatos contidos no perfil do profissional.
        """

        try:
            draft = await llm_service.get_structured_output(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=ApplicationDraftSchema
            )
            # Save to cache
            self._draft_cache[cache_key] = draft
            return draft
        except Exception as e:
            logger.error(f"Failed to generate application draft: {e}")
            raise e


application_generator_service = ApplicationGeneratorService()

import os
import hashlib
import json
import logging
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models.db_models import Resume, UserProfile, Experience, Project
from app.schemas.schemas import ParsedResumeSchema
from app.services.llm_service import llm_service

logger = logging.getLogger("job_hunter.resume_service")

def calculate_sha256(content: bytes) -> str:
    """Calculates SHA256 of file bytes."""
    return hashlib.sha256(content).hexdigest()

def extract_text_from_pdf(pdf_path: str) -> str:
    """Reads PDF and extracts all text."""
    try:
        reader = PdfReader(pdf_path)
        text = ""
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text += f"--- Page {i+1} ---\n{page_text}\n"
        if not text.strip():
            raise ValueError("No text could be extracted from this PDF. It might be scanned or empty.")
        return text
    except Exception as e:
        logger.error(f"Failed to read PDF at {pdf_path}: {e}")
        raise e

async def parse_resume_content(text: str) -> ParsedResumeSchema:
    """Invokes LLM with system instructions to turn CV plain text into a structured Pydantic schema."""
    system_prompt = (
        "Você é uma inteligência artificial especialista em RH e recrutamento de tecnologia no Brasil.\n"
        "Seu papel é receber o texto bruto de um currículo e estruturá-lo rigorosamente.\n"
        "Extraia e divida as tecnologias e termos nas categorias corretas:\n"
        "- cloud_providers (AWS, Azure, GCP, etc.)\n"
        "- devops_tools (Docker, Kubernetes, Terraform, Git, CI/CD, etc.)\n"
        "- databases (PostgreSQL, MongoDB, SQL Server, etc.)\n"
        "- technologies (Linguagens como Python, JavaScript, framework Django, React, etc.)\n"
        "Seja preciso. Não invente nenhuma informação que não esteja contida explicitamente no texto."
    )
    user_prompt = f"Aqui está o texto bruto do currículo profissional:\n\n{text}"
    
    parsed_cv = await llm_service.get_structured_output(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=ParsedResumeSchema
    )
    return parsed_cv

async def update_user_profile_from_parsed_resume(db: AsyncSession, parsed_data: ParsedResumeSchema):
    """Upserts/Seeds the core UserProfile, Experiences, and Projects from the uploaded resume."""
    try:
        # Check if a profile already exists. For simplicity, we assume a single user profile.
        result = await db.execute(select(UserProfile).limit(1))
        existing_profile = result.scalars().first()

        if existing_profile:
            # Update root fields
            existing_profile.name = parsed_data.name
            existing_profile.education = parsed_data.education
            existing_profile.desired_roles = parsed_data.desired_roles
            existing_profile.technologies = parsed_data.technologies
            existing_profile.languages = parsed_data.languages
            existing_profile.cloud_providers = parsed_data.cloud_providers
            existing_profile.devops_tools = parsed_data.devops_tools
            existing_profile.databases = parsed_data.databases
            existing_profile.certifications = parsed_data.certifications
            existing_profile.location = parsed_data.location
            existing_profile.work_mode = parsed_data.work_mode
            existing_profile.professional_goals = parsed_data.professional_goals
            profile = existing_profile
            
            # Clear old experiences and projects to rewrite them fresh
            # Using clean ORM cascade deletion by fetching the instances
            await db.execute(select(Experience).where(Experience.user_profile_id == profile.id))
            await db.execute(select(Project).where(Project.user_profile_id == profile.id))
        else:
            profile = UserProfile(
                name=parsed_data.name,
                education=parsed_data.education,
                desired_roles=parsed_data.desired_roles,
                technologies=parsed_data.technologies,
                languages=parsed_data.languages,
                cloud_providers=parsed_data.cloud_providers,
                devops_tools=parsed_data.devops_tools,
                databases=parsed_data.databases,
                certifications=parsed_data.certifications,
                location=parsed_data.location,
                work_mode=parsed_data.work_mode,
                professional_goals=parsed_data.professional_goals
            )
            db.add(profile)
            await db.flush() # Secure profile.id

        # Insert experiences
        for exp in parsed_data.experiences:
            db_exp = Experience(
                user_profile_id=profile.id,
                company=exp.company,
                role=exp.role,
                start_date=exp.start_date,
                end_date=exp.end_date,
                description=exp.description,
                skills_used=exp.skills_used
            )
            db.add(db_exp)

        # Insert projects
        for proj in parsed_data.projects:
            db_proj = Project(
                user_profile_id=profile.id,
                name=proj.name,
                description=proj.description,
                technologies=proj.technologies,
                url=proj.url
            )
            db.add(db_proj)

        await db.commit()
    except Exception as e:
        logger.error(f"Error seeding user profile from resume: {e}")
        await db.rollback()
        raise e

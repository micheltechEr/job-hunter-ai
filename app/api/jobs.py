import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models.db_models import Job, JobAnalysis, Application, UserProfile
from app.schemas.schemas import JobCreate, JobResponse, MatchResponse, ApplicationResponse
from app.services.job_analysis import job_analysis_service
from app.services.matching import matching_service
from app.services.application_generator import application_generator_service

router = APIRouter()
logger = logging.getLogger("job_hunter.api.jobs")

@router.post("/", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(job_in: JobCreate, db: AsyncSession = Depends(get_db)):
    """Receives a new job, extracts metrics via LLM, and persists both records."""
    try:
        # 1. Create and save job
        job = Job(
            title=job_in.title,
            company=job_in.company,
            url=str(job_in.url) if job_in.url else None,
            description=job_in.description,
            location=job_in.location,
            work_mode=job_in.work_mode,
            salary=job_in.salary
        )
        db.add(job)
        await db.flush() # obtain job.id

        # 2. Extract job metrics using LLM analysis service
        analysis_data = await job_analysis_service.analyze_job_description(job.description)
        
        # 3. Create job analysis record
        db_analysis = JobAnalysis(
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
            raw_json=analysis_data.dict()
        )
        db.add(db_analysis)
        await db.commit()
        
        result = await db.execute(
            select(Job)
            .options(selectinload(Job.analysis), selectinload(Job.application))
            .where(Job.id == job.id)
        )
        return result.scalars().first()
    except Exception as e:
        logger.error(f"Failed to create and analyze job: {e}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Erro de processamento da análise do descritivo da vaga: {str(e)}"
        )

@router.get("/", response_model=List[JobResponse])
async def list_jobs(
    min_score: Optional[int] = Query(None, description="Filtra vagas com score ATS mínimo (ex: 80)"),
    db: AsyncSession = Depends(get_db)
):
    """Lists jobs with eager loading of analysis and application, supporting ATS minimum score filtering."""
    result = await db.execute(
        select(Job)
        .options(selectinload(Job.analysis), selectinload(Job.application))
        .order_by(Job.created_at.desc())
    )
    jobs = result.scalars().all()
    if min_score is not None:
        jobs = [j for j in jobs if j.application and j.application.score is not None and j.application.score >= min_score]
    return jobs

@router.delete("/clear", status_code=status.HTTP_200_OK)
async def clear_all_jobs(db: AsyncSession = Depends(get_db)):
    """Clears all jobs and cascaded records from the database."""
    result = await db.execute(select(Job))
    jobs = result.scalars().all()
    count = len(jobs)
    for j in jobs:
        await db.delete(j)
    await db.commit()
    logger.info(f"Cleared all {count} jobs from database.")
    return {"message": f"Todas as {count} vagas foram removidas com sucesso.", "count": count}

@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Job)
        .options(selectinload(Job.analysis), selectinload(Job.application))
        .where(Job.id == job_id)
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Vaga não encontrada.")
    return job

@router.delete("/{job_id}", status_code=status.HTTP_200_OK)
async def delete_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Deletes a specific job and cascaded records."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Vaga não encontrada.")
    await db.delete(job)
    await db.commit()
    logger.info(f"Job {job_id} deleted.")
    return {"message": "Vaga removida com sucesso.", "id": job_id}

@router.post("/{job_id}/match", response_model=MatchResponse)
async def match_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Computes similarity metrics and matches candidate CV qualifications with job traits applying ATS methodology."""
    try:
        match_result = await matching_service.match_job_profile(db, job_id)
        return match_result
    except ValueError as val_ex:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_ex))
    except Exception as e:
        logger.error(f"Error matching job {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro durante cálculo de match: {str(e)}"
        )

@router.post("/{job_id}/generate-application", response_model=ApplicationResponse)
async def generate_application_endpoint(job_id: int, db: AsyncSession = Depends(get_db)):
    """Triggers complete matching checks, recommends CV, writes email draft, and creates an Application record in REVIEW status."""
    try:
        # 1. Perform match to choose CV and get score
        match_res = await match_job(job_id, db)
        
        # 2. Fetch Job and its Analysis
        result = await db.execute(select(Job).options(selectinload(Job.analysis)).where(Job.id == job_id))
        job = result.scalars().first()
        result_analysis = await db.execute(select(JobAnalysis).where(JobAnalysis.job_id == job_id))
        job_analysis = result_analysis.scalars().first()

        # 3. Fetch User Profile
        result_profile = await db.execute(select(UserProfile).options(selectinload(UserProfile.experiences), selectinload(UserProfile.projects)).limit(1))
        user_profile = result_profile.scalars().first()
        if not user_profile:
             raise HTTPException(status_code=400, detail="Perfil de usuário ausente. Carregue um currículo antes.")

        # 4. Generate email draft using AI application generator service
        draft = await application_generator_service.generate_draft(user_profile, job, job_analysis)

        # 5. Check if user already generated an application for this job
        result_app = await db.execute(select(Application).where(Application.job_id == job_id))
        existing_app = result_app.scalars().first()

        recipient = draft.recipient_email if draft.recipient_email else ""

        if existing_app:
            # Update draft info
            existing_app.resume_id = match_res.recommended_resume_id
            existing_app.recipient_email = recipient
            existing_app.score = match_res.score
            existing_app.fit = match_res.fit
            existing_app.email_subject = draft.subject
            existing_app.email_body = draft.body
            existing_app.status = "REVIEW" # Set review status for approval
            await db.commit()
            await db.refresh(existing_app)
            return existing_app

        # Create new application tracking record
        app_record = Application(
            job_id=job.id,
            resume_id=match_res.recommended_resume_id,
            recipient_email=recipient,
            score=match_res.score,
            fit=match_res.fit,
            email_subject=draft.subject,
            email_body=draft.body,
            status="REVIEW" # Waiting human-in-the-loop approval
        )
        db.add(app_record)
        await db.commit()
        await db.refresh(app_record)
        return app_record

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Candidacy draft preparation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro na preparação do rascunho de candidatura: {str(e)}"
        )

@router.post("/scrape/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger_jobs_scraping():
    """Human-triggered instant background scrape for target jobs."""
    from app.services.scheduler import run_job_hunting_scrape
    import asyncio
    asyncio.create_task(run_job_hunting_scrape())
    return {"message": "Varredura iniciada em segundo plano com base nos cargos salvos."}

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import or_

from app.database import get_db
from app.models.db_models import Job, JobAnalysis, Application, UserProfile
from app.schemas.schemas import JobCreate, JobResponse, MatchResponse, ApplicationResponse
from app.services.job_analysis import job_analysis_service
from app.services.matching import matching_service
from app.services.application_generator import application_generator_service
from app.services.ats_queue import ats_worker_queue

router = APIRouter()
logger = logging.getLogger("job_hunter.api.jobs")


@router.post("/", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(job_in: JobCreate, db: AsyncSession = Depends(get_db)):
    """
    Receives a new job, persists it instantly to database so it is immediately searchable,
    and enqueues background ATS analysis to avoid blocking user interaction.
    """
    try:
        # 1. Create and save job record immediately
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
        await db.flush()

        # 2. Attach initial placeholder application
        init_app = Application(
            job_id=job.id,
            status="ANALYZING"
        )
        db.add(init_app)
        await db.commit()

        # 3. Enqueue background ATS calculation (Non-blocking)
        await ats_worker_queue.enqueue(job.id)

        # 4. Fetch full job object with relations
        result = await db.execute(
            select(Job)
            .options(selectinload(Job.analysis), selectinload(Job.application))
            .where(Job.id == job.id)
        )
        return result.scalars().first()

    except Exception as e:
        logger.error(f"Failed to create job: {e}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Erro ao cadastrar vaga: {str(e)}"
        )


@router.get("/", response_model=List[JobResponse])
async def list_jobs(
    search: Optional[str] = Query(None, description="Busca rápida por palavra-chave em cargo, empresa, localização ou descrição"),
    min_score: Optional[int] = Query(None, description="Filtra vagas com score ATS mínimo (ex: 80)"),
    status_filter: Optional[str] = Query(None, description="Filtra por status da candidatura (ex: HIGH_MATCH, ANALYZING, DISCOVERED)"),
    work_mode: Optional[str] = Query(None, description="Filtra por modalidade (Remote, Hybrid, On-site)"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists and searches jobs instantly while ATS generation runs concurrently in the background.
    Supports multi-field keyword search, ATS score filtering, and work mode filters.
    """
    query = (
        select(Job)
        .options(selectinload(Job.analysis), selectinload(Job.application))
        .order_by(Job.created_at.desc())
    )

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Job.title.ilike(term),
                Job.company.ilike(term),
                Job.location.ilike(term),
                Job.description.ilike(term)
            )
        )

    if work_mode and work_mode.strip():
        query = query.where(Job.work_mode.ilike(f"%{work_mode.strip()}%"))

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    jobs = result.scalars().all()

    # In-memory post-filtering for application relationships (score / status)
    filtered_jobs = []
    for j in jobs:
        if min_score is not None:
            if not j.application or j.application.score is None or j.application.score < min_score:
                continue
        if status_filter and status_filter.strip():
            if not j.application or (j.application.status or "").upper() != status_filter.strip().upper():
                continue
        filtered_jobs.append(j)

    return filtered_jobs


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
    return {"message": f"Removidas todas as {count} vagas com sucesso.", "cleared_count": count}


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Retrieves a single job with analysis and application details."""
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
    """Triggers complete ATS evaluation and drafts candidate personalized application."""
    try:
        res = await db.execute(
            select(Job)
            .options(selectinload(Job.analysis), selectinload(Job.application))
            .where(Job.id == job_id)
        )
        job = res.scalars().first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")

        # Ensure analysis exists
        if not job.analysis:
            analysis_data = await job_analysis_service.analyze_job_description(job.description)
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
                raw_json=analysis_data.model_dump() if hasattr(analysis_data, "model_dump") else analysis_data.dict()
            )
            db.add(db_analysis)
            await db.commit()
            await db.refresh(job)

        match_res = await matching_service.match_job_profile(db, job_id)

        res_prof = await db.execute(
            select(UserProfile)
            .options(selectinload(UserProfile.experiences), selectinload(UserProfile.projects))
            .limit(1)
        )
        user_prof = res_prof.scalars().first()
        if not user_prof:
            raise HTTPException(status_code=400, detail="User profile not configured.")

        draft = await application_generator_service.generate_draft(user_prof, job, job.analysis)

        res_app = await db.execute(select(Application).where(Application.job_id == job_id))
        app_record = res_app.scalars().first()

        recipient = draft.recipient_email if draft.recipient_email else (app_record.recipient_email if app_record else None)

        if app_record:
            app_record.resume_id = match_res.recommended_resume_id
            app_record.recipient_email = recipient
            app_record.score = match_res.score
            app_record.fit = match_res.fit
            app_record.email_subject = draft.subject
            app_record.email_body = draft.body
            app_record.status = "REVIEW" if match_res.score >= 60 else "DISCOVERED"
        else:
            app_record = Application(
                job_id=job_id,
                resume_id=match_res.recommended_resume_id,
                recipient_email=recipient,
                score=match_res.score,
                fit=match_res.fit,
                email_subject=draft.subject,
                email_body=draft.body,
                status="REVIEW"
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

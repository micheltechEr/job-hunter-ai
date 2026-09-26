import asyncio
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import or_

from app.database import get_db
from app.models.db_models import Job, JobAnalysis, Application, UserProfile, Resume
from app.schemas.schemas import (
    JobCreate,
    JobResponse,
    MatchResponse,
    ApplicationResponse,
    ResumeResponse,
    ScrapeTriggerRequest,
    ScrapeTriggerResponse,
    TailorResumeRequest
)
from app.services.job_analysis import job_analysis_service
from app.services.matching import matching_service
from app.services.application_generator import application_generator_service
from app.services.ats_queue import ats_worker_queue
from app.services.scraper_service import is_senior_title

router = APIRouter()
logger = logging.getLogger("job_hunter.api.jobs")


@router.post("/", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(job_in: JobCreate, db: AsyncSession = Depends(get_db)):
    """
    Receives a new job, persists it instantly to database with on-demand ATS evaluation.
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

        # 2. Attach initial placeholder application (DISCOVERED, ready for on-demand ATS on click)
        init_app = Application(
            job_id=job.id,
            status="DISCOVERED"
        )
        db.add(init_app)
        await db.commit()

        # 3. Fetch full job object with relations
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
    seniority: Optional[str] = Query(None, description="Filtra por senioridade (Junior, Pleno, Senior, etc.)"),
    exclude_senior: bool = Query(False, description="Oculta vagas com perfil Sênior/Lead/Staff"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists and searches jobs instantly while ATS generation runs concurrently in the background.
    Supports multi-field keyword search, ATS score filtering, work mode and seniority filters.
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

    # In-memory post-filtering for application relationships (score / status / seniority)
    filtered_jobs = []
    for j in jobs:
        if min_score is not None:
            if not j.application or j.application.score is None or j.application.score < min_score:
                continue
        if status_filter and status_filter.strip():
            if not j.application or (j.application.status or "").upper() != status_filter.strip().upper():
                continue
        
        job_is_senior = (j.analysis and j.analysis.seniority and "senior" in j.analysis.seniority.lower()) or is_senior_title(j.title)
        
        if exclude_senior and job_is_senior:
            continue

        if seniority and seniority.strip() and seniority.strip().lower() != "all":
            sen_req = seniority.strip().lower()
            job_sen = (j.analysis.seniority if j.analysis and j.analysis.seniority else "").lower()
            if sen_req in ["junior", "jr", "estagio", "estágio", "junior/pleno"]:
                if job_is_senior:
                    continue
            elif sen_req in ["pleno", "mid"]:
                if job_is_senior:
                    continue
            elif sen_req == "senior":
                if not job_is_senior:
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
    """Computes similarity metrics and matches candidate CV qualifications with job traits applying ATS methodology on-demand."""
    try:
        match_result = await matching_service.match_job_profile(db=db, job_id=job_id)

        # Persist score and fit to the application record in database
        res_app = await db.execute(select(Application).where(Application.job_id == job_id))
        app = res_app.scalars().first()
        if not app:
            app = Application(job_id=job_id)
            db.add(app)
        app.score = match_result.score
        app.fit = match_result.fit
        app.resume_id = match_result.recommended_resume_id
        if match_result.explanation:
            app.notes = match_result.explanation
        if app.status in ("DISCOVERED", "ANALYZING", None):
            app.status = "HIGH_MATCH" if match_result.score >= 80 else ("REVIEW" if match_result.score >= 60 else "DISCOVERED")
        await db.commit()
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
    """Triggers complete ATS evaluation and drafts candidate personalized application with parallel LLM execution."""
    try:
        # 1. Fetch Job with loaded relationships
        res_job = await db.execute(
            select(Job)
            .options(selectinload(Job.analysis), selectinload(Job.application))
            .where(Job.id == job_id)
        )
        job = res_job.scalars().first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")

        # Fast path: If application is already generated and ready, return immediately (<2ms)
        if job.application and job.application.email_body and job.application.score is not None and job.application.status != "ANALYZING":
            return job.application

        # 2. Fetch User Profile and Resumes sequentially on the same session
        res_prof = await db.execute(
            select(UserProfile)
            .options(selectinload(UserProfile.experiences), selectinload(UserProfile.projects))
            .limit(1)
        )
        user_prof = res_prof.scalars().first()
        if not user_prof:
            raise HTTPException(status_code=400, detail="User profile not configured.")

        res_resumes = await db.execute(select(Resume))
        resumes = res_resumes.scalars().all()

        # 3. Ensure analysis exists (create once if missing)
        target_analysis = job.analysis
        if not target_analysis:
            analysis_data = await job_analysis_service.analyze_job_description(job.description or job.title)
            target_analysis = JobAnalysis(
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
            db.add(target_analysis)
            await db.commit()
            await db.refresh(target_analysis)

        # 4. Parallel Execution: Run ATS Match and Application Email Draft concurrently
        match_task = matching_service.match_job_profile(
            db=db,
            job_id=job_id,
            job=job,
            job_analysis=target_analysis,
            user_profile=user_prof,
            resumes=resumes
        )
        draft_task = application_generator_service.generate_draft(
            user_profile=user_prof,
            job=job,
            job_class=target_analysis
        )

        match_res, draft = await asyncio.gather(match_task, draft_task)

        # 5. Attach or update application record
        app_record = job.application
        recipient = draft.recipient_email if draft.recipient_email else (app_record.recipient_email if app_record else None)

        if app_record:
            app_record.resume_id = match_res.recommended_resume_id
            app_record.recipient_email = recipient
            app_record.score = match_res.score
            app_record.fit = match_res.fit
            app_record.email_subject = draft.subject
            app_record.email_body = draft.body
            app_record.notes = match_res.explanation or app_record.notes
            app_record.status = "HIGH_MATCH" if match_res.score >= 80 else ("REVIEW" if match_res.score >= 60 else "DISCOVERED")
        else:
            app_record = Application(
                job_id=job_id,
                resume_id=match_res.recommended_resume_id,
                recipient_email=recipient,
                score=match_res.score,
                fit=match_res.fit,
                email_subject=draft.subject,
                email_body=draft.body,
                notes=match_res.explanation,
                status="HIGH_MATCH" if match_res.score >= 80 else ("REVIEW" if match_res.score >= 60 else "DISCOVERED")
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


@router.post("/{job_id}/tailor-resume", response_model=ResumeResponse)
async def tailor_job_resume(
    job_id: int,
    payload: Optional[TailorResumeRequest] = None,
    include_seniority: bool = Query(False, description="Se true, mantém sufixos de senioridade (Jr, Pleno, Sr) nos cargos"),
    db: AsyncSession = Depends(get_db)
):
    """Adapts candidate CV specifically for this job, regenerates ATS PDF and attaches it to the application."""
    from app.services.resume_service import tailor_and_save_resume_for_job
    try:
        inc_sen = payload.include_seniority if payload is not None else include_seniority
        resume = await tailor_and_save_resume_for_job(db, job_id, include_seniority=inc_sen)
        return resume
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to tailor resume for job {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao adaptar currículo para a vaga: {str(e)}"
        )


@router.post("/scrape/trigger", response_model=ScrapeTriggerResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_jobs_scraping(
    payload: Optional[ScrapeTriggerRequest] = None,
    db: AsyncSession = Depends(get_db)
):
    """Human-triggered instant background scrape for target jobs based on specified or profile roles."""
    from app.services.scheduler import run_job_hunting_scrape
    import asyncio

    target_roles: List[str] = []
    if payload:
        if payload.role and payload.role.strip():
            for part in payload.role.split(","):
                clean = part.strip()
                if clean and clean not in target_roles:
                    target_roles.append(clean)
        if payload.roles:
            for r in payload.roles:
                clean = r.strip() if isinstance(r, str) else ""
                if clean and clean not in target_roles:
                    target_roles.append(clean)

    res_prof = await db.execute(select(UserProfile).limit(1))
    prof = res_prof.scalars().first()

    # If no custom roles passed, fetch existing profile roles
    if not target_roles:
        if prof and prof.desired_roles:
            target_roles = [r.strip() for r in prof.desired_roles if r and r.strip()]
        else:
            target_roles = ["Desenvolvedor Python", "Engenheiro de Software"]

    target_location = (payload.location.strip() if (payload and payload.location and payload.location.strip()) else "Brasil")
    target_platforms = (payload.platforms if (payload and payload.platforms) else ["linkedin", "gupy", "programathor", "indeed", "infojobs", "trabalhabrasil"])
    target_limit = (payload.limit_per_platform if (payload and payload.limit_per_platform) else 5)
    save_to_profile = (payload.save_to_profile if payload else False)
    target_seniority = payload.seniority if (payload and payload.seniority) else (prof.seniority_level if prof and prof.seniority_level else "Junior")
    exclude_senior = payload.exclude_senior if (payload and payload.exclude_senior is not None) else True

    # Trigger background scraping
    asyncio.create_task(
        run_job_hunting_scrape(
            roles=target_roles,
            location=target_location,
            seniority=target_seniority,
            platforms=target_platforms,
            limit_per_platform=target_limit,
            exclude_senior=exclude_senior,
            save_to_profile=save_to_profile
        )
    )

    platform_names = ", ".join([p.capitalize() for p in target_platforms])
    roles_names = ", ".join(target_roles)
    sen_str = f" [{target_seniority}]" if target_seniority else ""
    msg = f"Varredura iniciada para: '{roles_names}'{sen_str} ({target_location}) nas plataformas: {platform_names}."

    return ScrapeTriggerResponse(
        message=msg,
        roles=target_roles,
        location=target_location,
        platforms=target_platforms,
        seniority=target_seniority,
        status="initiated"
    )

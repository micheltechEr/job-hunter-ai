import os
import shutil
import logging
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.config import settings
from app.models.db_models import Resume, UserProfile
from app.services.resume_service import (
    calculate_sha256,
    extract_text_from_pdf,
    parse_resume_content,
    update_user_profile_from_parsed_resume,
    tailor_and_save_resume_for_job
)
from app.schemas.schemas import ResumeResponse, UserProfileResponse, UpdateProfileRolesRequest

router = APIRouter()
logger = logging.getLogger("job_hunter.api.resumes")

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB size limit restriction

@router.post("/upload", response_model=ResumeResponse, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    version_name: str = Form(..., description="E.g., base, backend, devops, cloud"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    # 1. Validate file format type
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Apenas arquivos PDF são aceitos profissionalmente."
        )

    # 2. Assert file size limits
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Arquivo excede o limite máximo permitido de 5MB."
        )

    # Reset file pointer for reading
    await file.seek(0)

    # 3. Prevent duplicate curriculum uploads using SHA256 hashes
    file_hash = calculate_sha256(content)
    result = await db.execute(select(Resume).where(Resume.file_hash == file_hash))
    existing_resume = result.scalars().first()
    if existing_resume:
        # Return existing resume reference instead of creating a duplicate
        return existing_resume

    # Ensure uploads folder path exists
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(settings.UPLOAD_DIR, f"{file_hash}_{file.filename}")

    # 4. Save file to Disk
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        logger.error(f"Error saving PDF path locally to disk: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao salvar arquivo no disco local."
        )

    # 5. Extract text, parse with LLM and update user profile
    try:
        text = extract_text_from_pdf(file_path)
        parsed_data = await parse_resume_content(text)
        
        # Seed UserProfile automatically
        await update_user_profile_from_parsed_resume(db, parsed_data)

        # Create resume entry
        resume = Resume(
            filename=file.filename,
            file_path=file_path,
            version_name=version_name,
            file_hash=file_hash,
            parsed_data=parsed_data.model_dump()
        )
        db.add(resume)
        await db.commit()
        await db.refresh(resume)
        
        return resume
    except Exception as e:
        logger.error(f"Failed to process and analyze uploaded PDF resume: {e}")
        # Clean file on error
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Erro no processamento de análise do currículo: {str(e)}"
        )

@router.get("/", response_model=List[ResumeResponse])
async def list_resumes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Resume))
    return result.scalars().all()

@router.get("/profile", response_model=Optional[UserProfileResponse])
async def get_current_user_profile(db: AsyncSession = Depends(get_db)):
    """Retrieves current user profile information including target roles and location."""
    result = await db.execute(select(UserProfile).limit(1))
    profile = result.scalars().first()
    if not profile:
        return None
    return profile

@router.post("/profile/roles", response_model=UserProfileResponse)
async def update_profile_roles(payload: UpdateProfileRolesRequest, db: AsyncSession = Depends(get_db)):
    """Updates target desired roles and optionally location / seniority on the user profile."""
    result = await db.execute(select(UserProfile).limit(1))
    profile = result.scalars().first()
    clean_roles = [r.strip() for r in payload.desired_roles if r and r.strip()]
    if not profile:
        profile = UserProfile(
            name="Candidato",
            desired_roles=clean_roles,
            location=payload.location or "Brasil",
            seniority_level=payload.seniority_level or "Junior"
        )
        db.add(profile)
    else:
        profile.desired_roles = clean_roles
        if payload.location:
            profile.location = payload.location.strip()
        if payload.seniority_level:
            profile.seniority_level = payload.seniority_level.strip()

    await db.commit()
    await db.refresh(profile)
    return profile

@router.get("/{resume_id}", response_model=ResumeResponse)
async def get_resume(resume_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Resume).where(Resume.id == resume_id))
    resume = result.scalars().first()
    if not resume:
        raise HTTPException(status_code=404, detail="Currículo não encontrado.")
    return resume


@router.get("/{resume_id}/file")
async def get_resume_file(resume_id: int, db: AsyncSession = Depends(get_db)):
    """Serves the PDF resume file inline for in-browser preview or download."""
    result = await db.execute(select(Resume).where(Resume.id == resume_id))
    resume = result.scalars().first()
    if not resume or not resume.file_path or not os.path.exists(resume.file_path):
        raise HTTPException(status_code=404, detail="Arquivo PDF do currículo não encontrado.")

    return FileResponse(
        resume.file_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{resume.filename}"'
        }
    )


@router.get("/{resume_id}/preview")
async def get_resume_preview(resume_id: int, db: AsyncSession = Depends(get_db)):
    """Returns structured metadata and file URL for rendering rich CV preview."""
    result = await db.execute(select(Resume).where(Resume.id == resume_id))
    resume = result.scalars().first()
    if not resume:
        raise HTTPException(status_code=404, detail="Currículo não encontrado.")

    return {
        "id": resume.id,
        "filename": resume.filename,
        "version_name": resume.version_name,
        "file_url": f"/api/resumes/{resume.id}/file",
        "parsed_data": resume.parsed_data or {},
        "created_at": resume.created_at
    }


@router.post("/tailor/{job_id}", response_model=ResumeResponse)
async def tailor_resume_endpoint(job_id: int, db: AsyncSession = Depends(get_db)):
    """Creates an AI-adapted, ATS-optimized PDF resume tailored specifically for the target job."""
    try:
        resume = await tailor_and_save_resume_for_job(db, job_id)
        return resume
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to tailor resume for job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao adaptar currículo para a vaga: {str(e)}"
        )

@router.delete("/{resume_id}", status_code=status.HTTP_200_OK)
async def delete_resume(resume_id: int, db: AsyncSession = Depends(get_db)):
    """Deletes a resume record from database and removes its physical file from disk."""
    result = await db.execute(select(Resume).where(Resume.id == resume_id))
    resume = result.scalars().first()
    if not resume:
        raise HTTPException(status_code=404, detail="Currículo não encontrado.")

    # Remove physical file if present
    if resume.file_path and os.path.exists(resume.file_path):
        try:
            os.remove(resume.file_path)
            logger.info(f"Deleted physical CV file: {resume.file_path}")
        except Exception as e:
            logger.warning(f"Could not remove physical CV file {resume.file_path}: {e}")

    await db.delete(resume)
    await db.commit()
    logger.info(f"Resume {resume_id} deleted successfully.")
    return {"message": "Currículo removido com sucesso.", "id": resume_id}

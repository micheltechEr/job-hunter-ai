import logging
import datetime
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models.db_models import Application, Resume, Job
from app.schemas.schemas import ApplicationResponse
from app.services.gmail_service import gmail_service

router = APIRouter()
logger = logging.getLogger("job_hunter.api.applications")


class ApplicationUpdateIn(BaseModel):
    recipient_email: Optional[str] = None
    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None # E.g., APPROVED, ARCHIVED


@router.get("/", response_model=List[ApplicationResponse])
async def list_applications(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Application).order_by(Application.created_at.desc()))
    return result.scalars().all()


@router.get("/{app_id}", response_model=ApplicationResponse)
async def get_application(app_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Application).where(Application.id == app_id))
    app_record = result.scalars().first()
    if not app_record:
        raise HTTPException(status_code=404, detail="Candidatura não encontrada.")
    return app_record


@router.put("/{app_id}", response_model=ApplicationResponse)
async def update_application(
    app_id: int, 
    update_in: ApplicationUpdateIn, 
    db: AsyncSession = Depends(get_db)
):
    """Allows human-in-the-loop manual adjustments to email parameters, note logs or status values."""
    result = await db.execute(select(Application).where(Application.id == app_id))
    app_record = result.scalars().first()
    if not app_record:
        raise HTTPException(status_code=404, detail="Candidatura não encontrada.")

    # Apply shifts
    if update_in.recipient_email is not None:
        app_record.recipient_email = update_in.recipient_email
    if update_in.email_subject is not None:
        app_record.email_subject = update_in.email_subject
    if update_in.email_body is not None:
        app_record.email_body = update_in.email_body
    if update_in.notes is not None:
        app_record.notes = update_in.notes
    if update_in.status is not None:
        app_record.status = update_in.status

    await db.commit()
    await db.refresh(app_record)
    return app_record


@router.post("/{app_id}/approve-and-send", response_model=ApplicationResponse)
async def approve_and_send_application(app_id: int, db: AsyncSession = Depends(get_db)):
    """Human-in-the-loop: manually triggers real Gmail delivery for approved candidacy drafts."""
    # 1. Fetch application
    result = await db.execute(select(Application).where(Application.id == app_id))
    app_record = result.scalars().first()
    if not app_record:
        raise HTTPException(status_code=404, detail="Candidatura não encontrada.")

    # 2. Check if recipient email address is valid
    if not app_record.recipient_email or "@" not in app_record.recipient_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Endereço de e-mail do destinatário está ausente ou inválido. Edite antes de enviar."
        )

    # 3. Check OAuth connection status
    if not gmail_service.is_authenticated():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Gmail API não autenticada. Acesse o fluxo OAuth primeiro para conectar a conta."
        )

    # 4. Fetch recommended resume binary path
    resume_path = None
    if app_record.resume_id:
        result_resume = await db.execute(select(Resume).where(Resume.id == app_record.resume_id))
        resume = result_resume.scalars().first()
        if resume:
            resume_path = resume.file_path

    # 5. Execute API Send call
    try:
        gmail_service.send_email(
            subject=app_record.email_subject or "Candidatura Profissional",
            body=app_record.email_body or "",
            recipient=app_record.recipient_email,
            attachment_path=resume_path
        )
        
        # 6. Update tracking details
        app_record.status = "SENT"
        app_record.sent_at = datetime.datetime.now()
        await db.commit()
        await db.refresh(app_record)
        return app_record

    except Exception as e:
        logger.error(f"Failed to dispatch application email: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha de conexão com a API do Gmail: {str(e)}"
        )


@router.post("/{app_id}/cancel", response_model=ApplicationResponse)
async def cancel_application(app_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Application).where(Application.id == app_id))
    app_record = result.scalars().first()
    if not app_record:
        raise HTTPException(status_code=404, detail="Candidatura não encontrada.")

    app_record.status = "ARCHIVED"
    await db.commit()
    await db.refresh(app_record)
    return app_record

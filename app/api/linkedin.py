import os
import asyncio
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks, status
from app.services.linkedin_auth import (
    get_linkedin_storage_state_path,
    get_default_linkedin_profile_dir,
    async_interactive_login
)
from app.config import settings

router = APIRouter()
logger = logging.getLogger("job_hunter.api.linkedin")


@router.get("/status")
async def get_linkedin_status():
    """Returns the current authentication and persistent session status for LinkedIn."""
    state_file = get_linkedin_storage_state_path()
    cookie_present = bool(settings.LINKEDIN_COOKIE_LI_AT)

    is_authenticated = bool(state_file or cookie_present)
    source_detail = f"storage_state ({state_file})" if state_file else "cookie li_at" if cookie_present else "anonymous/guest"

    return {
        "authenticated": is_authenticated,
        "mode": "authenticated" if is_authenticated else "anonymous",
        "storage_state_path": str(state_file) if state_file else None,
        "source": source_detail,
        "message": "Sessão do LinkedIn ativa e persistente." if is_authenticated else "Modo visitante ativo (Buscas públicas sem login)."
    }


@router.post("/connect")
async def connect_linkedin(background_tasks: BackgroundTasks):
    """
    Triggers an interactive Chromium window for one-time login.
    Saves session cookies to storage_state.json.
    """
    try:
        from app.services.scraper_service import _run_in_proactor_thread
        from app.services.linkedin_auth import interactive_login

        # Launch login in dedicated background proactor thread
        asyncio.create_task(_run_in_proactor_thread(interactive_login))

        return {
            "status": "initiated",
            "message": "Janela do navegador aberta. Faça login no LinkedIn e pressione ENTER no terminal para salvar a sessão."
        }
    except Exception as e:
        logger.error(f"Failed to start interactive LinkedIn login: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disconnect")
async def disconnect_linkedin():
    """Removes the local persistent storage state file."""
    state_file = get_linkedin_storage_state_path()
    if state_file and state_file.exists():
        try:
            os.remove(state_file)
            return {"status": "disconnected", "message": "Sessão do LinkedIn removida com sucesso."}
        except Exception as e:
            logger.error(f"Failed to remove LinkedIn storage state: {e}")
            raise HTTPException(status_code=500, detail=f"Erro ao remover arquivo: {e}")

    return {"status": "no_session", "message": "Nenhuma sessão ativa encontrada para remover."}

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, status, Query
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from app.services.gmail_service import gmail_service

router = APIRouter()
logger = logging.getLogger("job_hunter.api.gmail")

@router.get("/status")
async def get_gmail_status():
    """Returns the current connection status and login account if authenticated."""
    authenticated = gmail_service.is_authenticated()
    if authenticated:
        try:
            profile = gmail_service.get_profile()
            return {
                "authenticated": True,
                "email": profile.get("emailAddress"),
                "messages_sent": profile.get("messagesTotal", 0)
            }
        except Exception as e:
            logger.error(f"Failed to fetch Gmail profile: {e}")
            return {
                "authenticated": True,
                "email": "Conectado (Erro ao obter perfil)",
                "error": str(e)
            }
    else:
        # Check if credentials.json is present
        creds_missing = not gmail_service.credentials_exist()
        return {
            "authenticated": False,
            "credentials_missing": creds_missing,
            "message": "credentials.json necessária na pasta raiz do projeto" if creds_missing else "Gmail não autenticado"
        }

@router.get("/auth")
@router.get("/auth-url")
async def start_authentication():
    """Helper that starts the OAuth connection and returns the Google approval redirection URL."""
    try:
        auth_url = gmail_service.get_auth_url()
        return {"auth_url": auth_url}
    except FileNotFoundError as fnf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(fnf)
        )
    except Exception as e:
        logger.error(f"Failed to build auth URL link: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao iniciar autenticação OAuth: {str(e)}"
        )

@router.get("/callback")
async def auth_callback(code: str = Query(...), state: Optional[str] = Query(None)):
    """Callback triggered by Google server redirecting after user grants authorizations."""
    try:
        email = await gmail_service.fetch_token_from_code(code=code, state=state)
        logger.info(f"Gmail successfully authenticated as {email}.")
        # Redirect the browser back to our home SPA dashboard
        return RedirectResponse(url="/?oauth=connected")
    except Exception as e:
        logger.error(f"Gmail exchange callback failed: {e}")
        return HTMLResponse(
            content=f"<h3>Erro de Autenticação OAuth</h3><p>{str(e)}</p><a href='/'>Voltar ao Dashboard</a>",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

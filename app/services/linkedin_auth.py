import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from app.config import settings

logger = logging.getLogger("job_hunter.linkedin_auth")


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_default_linkedin_profile_dir() -> Path:
    """
    Returns the persistent LinkedIn browser profile directory inside the job-hunter-ai project.
    """
    if settings.LINKEDIN_BROWSER_PROFILE_DIR:
        p = Path(settings.LINKEDIN_BROWSER_PROFILE_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    local_p = PROJECT_ROOT / "browser_profile"
    local_p.mkdir(parents=True, exist_ok=True)
    return local_p


def get_linkedin_storage_state_path() -> Optional[Path]:
    """
    Locates an existing storage_state.json with authenticated LinkedIn session cookies inside job-hunter-ai.
    """
    if settings.LINKEDIN_STORAGE_STATE_PATH:
        p = Path(settings.LINKEDIN_STORAGE_STATE_PATH)
        if p.exists() and p.is_file():
            return p

    # 1. Check local project directory first (self-contained inside job-hunter-ai)
    local_state = PROJECT_ROOT / "browser_profile" / "storage_state.json"
    if local_state.exists() and local_state.is_file():
        return local_state

    # 2. Check shared user workspace directory fallback
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    shared_state = home / ".linkedin_optimizer_workspace" / "browser_profile" / "storage_state.json"
    if shared_state.exists() and shared_state.is_file():
        return shared_state

    return None


async def async_interactive_login(target_url: str = "https://www.linkedin.com/login") -> Path:
    """
    Launches a visible Chromium window for one-time interactive login and saves storage_state.json.
    """
    from playwright.async_api import async_playwright
    profile_dir = get_default_linkedin_profile_dir()
    state_file = profile_dir / "storage_state.json"

    logger.info(f"Opening visible browser for LinkedIn login. Session will be saved to: {state_file}")
    print(f"\n[LinkedIn Auth] Abrindo navegador para autenticação manual...")
    print(f"[LinkedIn Auth] Faça login no LinkedIn. Quando estiver no feed ou perfil, volte aqui.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")

        # Wait for user confirmation in terminal or detect navigation to feed
        print("\n--> Após fazer login e ver o feed do LinkedIn, pressione ENTER no terminal para salvar a sessão...")
        input("Pressione ENTER após concluir o login no navegador: ")

        await context.storage_state(path=str(state_file))
        await browser.close()

    print(f"[LinkedIn Auth] Sessão salva com sucesso em: {state_file}")
    logger.info(f"LinkedIn persistent storage state saved to {state_file}")
    return state_file


def interactive_login(target_url: str = "https://www.linkedin.com/login") -> None:
    """CLI synchronous entrypoint for interactive login."""
    import asyncio
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(async_interactive_login(target_url))


if __name__ == "__main__":
    interactive_login()

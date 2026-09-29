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


def is_valid_storage_state(path: Optional[Path]) -> bool:
    """Checks if a storage_state.json contains the active session cookie (li_at)."""
    if not path or not path.exists() or not path.is_file():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cookies = data.get("cookies", [])
        return any(c.get("name") == "li_at" for c in cookies)
    except Exception:
        return False


def get_linkedin_storage_state_path() -> Optional[Path]:
    """
    Locates an existing storage_state.json with authenticated LinkedIn session cookies inside job-hunter-ai.
    """
    if settings.LINKEDIN_STORAGE_STATE_PATH:
        p = Path(settings.LINKEDIN_STORAGE_STATE_PATH)
        if is_valid_storage_state(p):
            return p

    # 1. Check local project directory first (self-contained inside job-hunter-ai)
    local_state = PROJECT_ROOT / "browser_profile" / "storage_state.json"
    if is_valid_storage_state(local_state):
        return local_state

    # 2. Check shared user workspace directory fallback
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    shared_state = home / ".linkedin_optimizer_workspace" / "browser_profile" / "storage_state.json"
    if is_valid_storage_state(shared_state):
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

        # Auto-detect login without blocking on terminal input
        logger.info("Waiting for user to authenticate in the open browser window...")
        print("\n--> [LinkedIn Auth] Faça login no navegador aberto. A sessão será detectada e salva automaticamente...")
        
        login_successful = False
        for _ in range(120):  # Wait up to 120 seconds for user login
            await page.wait_for_timeout(1000)
            try:
                cookies = await context.cookies()
                has_li_at = any(c.get("name") == "li_at" for c in cookies)
                current_url = page.url
                if has_li_at or "/feed" in current_url or "/in/" in current_url or "/mynetwork" in current_url:
                    login_successful = True
                    logger.info("LinkedIn login detected successfully!")
                    print("[LinkedIn Auth] Login detectado com sucesso!")
                    await page.wait_for_timeout(2000)
                    break
            except Exception:
                # Page or browser might be closing
                break

        if login_successful:
            await context.storage_state(path=str(state_file))
            print(f"[LinkedIn Auth] Sessão salva com sucesso em: {state_file}")
            logger.info(f"LinkedIn persistent storage state saved to {state_file}")
        else:
            logger.warning("[LinkedIn Auth] Timeout de login ou autenticação não concluída.")

        try:
            await browser.close()
        except Exception:
            pass

    return state_file


def interactive_login(target_url: str = "https://www.linkedin.com/login") -> None:
    """CLI synchronous entrypoint for interactive login."""
    import asyncio
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(async_interactive_login(target_url))


if __name__ == "__main__":
    interactive_login()

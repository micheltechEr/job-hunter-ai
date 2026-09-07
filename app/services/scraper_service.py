import asyncio
import sys
import logging
import urllib.parse
from typing import List, Dict, Callable, Any

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models.db_models import Job
from app.schemas.schemas import JobCreate
from app.api.jobs import create_job

logger = logging.getLogger("job_hunter.scraper_service")


def _run_in_proactor_thread(coro_fn: Callable, *args, **kwargs) -> Any:
    """Executes Playwright coroutines in a dedicated OS thread with WindowsProactorEventLoop.
    
    This avoids NotImplementedError on Windows when FastAPI/Uvicorn is running under SelectorEventLoop.
    """
    def worker():
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro_fn(*args, **kwargs))
        finally:
            loop.close()
    return asyncio.to_thread(worker)


class ScraperService:
    async def scrape_linkedin_jobs(self, keyword: str, location: str = "Brasil", limit: int = 5) -> List[Dict]:
        """Scrapes public LinkedIn job posts using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_linkedin_impl, keyword, location, limit)

    async def _scrape_linkedin_impl(self, keyword: str, location: str, limit: int) -> List[Dict]:
        jobs_scraped = []
        kw_encoded = urllib.parse.quote(keyword)
        loc_encoded = urllib.parse.quote(location)
        url = f"https://www.linkedin.com/jobs/search?keywords={kw_encoded}&location={loc_encoded}&f_TPR=r604800&position=1&pageNum=0"
        logger.info(f"Scraping LinkedIn: {url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                for _ in range(2):
                    await page.mouse.wheel(0, 800)
                    await asyncio.sleep(1)

                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select(".jobs-search__results-list li")
                
                count = 0
                for card in cards:
                    if count >= limit:
                        break
                    title_el = card.select_one(".base-search-card__title")
                    company_el = card.select_one(".base-search-card__subtitle")
                    link_el = card.select_one(".base-card__full-link")
                    loc_el = card.select_one(".job-search-card__location")
                    
                    if not title_el or not company_el or not link_el:
                        continue
                        
                    job_title = title_el.get_text().strip()
                    company = company_el.get_text().strip()
                    job_url = link_el["href"].split("?")[0]
                    loc_text = loc_el.get_text().strip() if loc_el else "Brasil"
                    desc = f"Vaga de {job_title} na empresa {company}. Localização: {loc_text}."
                    work_mode = "Remote" if "remoto" in desc.lower() or "remote" in desc.lower() else "Hybrid" if "hibrid" in desc.lower() or "híbrid" in desc.lower() else "On-site"
                    
                    # Fetch details
                    try:
                        detail_page = await context.new_page()
                        await detail_page.goto(job_url, timeout=15000, wait_until="domcontentloaded")
                        detail_html = await detail_page.content()
                        await detail_page.close()
                        
                        detail_soup = BeautifulSoup(detail_html, "html.parser")
                        desc_el = detail_soup.select_one(".description__text, .show-more-less-html__markup")
                        if desc_el:
                            desc = desc_el.get_text("\n").strip()
                            work_mode = "Remote" if "remoto" in desc.lower() or "remote" in desc.lower() else "Hybrid" if "hibrid" in desc.lower() or "híbrid" in desc.lower() else "On-site"
                    except Exception as det_err:
                        logger.debug(f"Could not load LinkedIn detail page: {det_err}")

                    jobs_scraped.append({
                        "title": job_title,
                        "company": company,
                        "url": job_url,
                        "description": desc,
                        "location": loc_text,
                        "work_mode": work_mode,
                        "salary": "N/A"
                    })
                    count += 1
            except Exception as e:
                logger.error(f"Error scraping LinkedIn: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    async def scrape_programathor_jobs(self, keyword: str, limit: int = 5) -> List[Dict]:
        """Scrapes jobs from Programathor (Brazilian Tech Job Board) using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_programathor_impl, keyword, limit)

    async def _scrape_programathor_impl(self, keyword: str, limit: int) -> List[Dict]:
        jobs_scraped = []
        kw_encoded = urllib.parse.quote(keyword)
        url = f"https://programathor.com.br/jobs?text={kw_encoded}"
        logger.info(f"Scraping Programathor: {url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select(".cell-list")
                
                count = 0
                for card in cards:
                    if count >= limit:
                        break
                    
                    link_el = card.select_one("a")
                    title_el = card.select_one(".cell-list-content h3")
                    
                    if not link_el or not title_el:
                        continue
                        
                    job_url = "https://programathor.com.br" + link_el["href"]
                    title = title_el.get_text().strip()
                    
                    spans = [s.get_text(strip=True) for s in card.select(".cell-list-content-icon span")]
                    company = spans[0] if len(spans) > 0 else "Empresa Confidencial"
                    location = spans[1] if len(spans) > 1 else "Brasil"
                    
                    desc = f"Vaga de {title} na empresa {company}. Localização: {location}."
                    work_mode = "Remote" if "remoto" in location.lower() or "remote" in location.lower() else "Hybrid" if "hibrid" in location.lower() or "híbrid" in location.lower() else "On-site"
                    salary = "N/A"
                    
                    # Try detail page
                    try:
                        detail_page = await context.new_page()
                        r = await detail_page.goto(job_url, timeout=10000, wait_until="domcontentloaded")
                        if r and r.status == 200:
                            detail_html = await detail_page.content()
                            detail_soup = BeautifulSoup(detail_html, "html.parser")
                            desc_el = detail_soup.select_one(".wrapper-content-job, .job-description")
                            if desc_el:
                                desc = desc_el.get_text("\n").strip()
                                work_mode = "Remote" if "remoto" in desc.lower() or "remote" in desc.lower() else "Hybrid" if "hibrid" in desc.lower() or "híbrid" in desc.lower() else "On-site"
                            salary_el = detail_soup.select_one(".fa-money-bill-alt")
                            if salary_el and salary_el.parent:
                                salary = salary_el.parent.get_text().strip()
                        await detail_page.close()
                    except Exception as det_err:
                        logger.debug(f"Could not load Programathor detail page: {det_err}")

                    jobs_scraped.append({
                        "title": title,
                        "company": company,
                        "url": job_url,
                        "description": desc,
                        "location": location,
                        "work_mode": work_mode,
                        "salary": salary
                    })
                    count += 1
            except Exception as e:
                logger.error(f"Error scraping Programathor: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    async def scrape_gupy_jobs(self, keyword: str, limit: int = 5) -> List[Dict]:
        """Scrapes jobs from Gupy Portal search engine using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_gupy_impl, keyword, limit)

    async def _scrape_gupy_impl(self, keyword: str, limit: int) -> List[Dict]:
        jobs_scraped = []
        kw_encoded = urllib.parse.quote(keyword)
        url = f"https://portal.gupy.io/job-search/term={kw_encoded}"
        logger.info(f"Scraping Gupy Portal: {url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="networkidle")
                
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                links = soup.find_all("a", href=True)
                job_links = [a for a in links if "/job/" in a["href"] or "jobId" in a["href"] or "/vaga/" in a["href"]]
                logger.info(f"Found {len(job_links)} Gupy job links.")
                
                count = 0
                for a in job_links:
                    if count >= limit:
                        break
                    
                    job_url = a["href"]
                    text_parts = [p_text.strip() for p_text in a.get_text(separator="|", strip=True).split("|") if p_text.strip()]
                    company = text_parts[0] if len(text_parts) > 0 else "Empresa Confidencial"
                    title = text_parts[1] if len(text_parts) > 1 else "Vaga Gupy"
                    
                    work_mode = "On-site"
                    for part in text_parts:
                        if "remoto" in part.lower():
                            work_mode = "Remote"
                            break
                        elif "híbrid" in part.lower() or "hibrid" in part.lower():
                            work_mode = "Hybrid"
                            break
                            
                    location = "Brasil"
                    for part in text_parts:
                        if " - " in part or "Brasil" in part or "Remoto" in part:
                            location = part
                            break

                    desc = f"Vaga de {title} na empresa {company}. Modalidade: {work_mode}. Localização: {location}."
                    
                    try:
                        detail_page = await context.new_page()
                        await detail_page.goto(job_url, timeout=15000, wait_until="domcontentloaded")
                        d_soup = BeautifulSoup(await detail_page.content(), "html.parser")
                        await detail_page.close()
                        desc_el = d_soup.select_one("[data-testid=\"text-section\"], article, main, .job-description, [class*=\"description\"]")
                        if desc_el:
                            desc = desc_el.get_text("\n").strip()
                    except Exception as det_err:
                        logger.debug(f"Could not load Gupy detail page: {det_err}")

                    jobs_scraped.append({
                        "title": title,
                        "company": company,
                        "url": job_url,
                        "description": desc,
                        "location": location,
                        "work_mode": work_mode,
                        "salary": "N/A"
                    })
                    count += 1
            except Exception as e:
                logger.error(f"Error scraping Gupy Portal: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    async def ingest_new_jobs(self, db: AsyncSession, scraped_jobs: List[Dict]):
        """Saves scraped jobs to the database and automatically triggers matching calculations."""
        for job_dict in scraped_jobs:
            try:
                # 1. Skip if already processed URL
                result = await db.execute(select(Job).where(Job.url == job_dict["url"]))
                existing = result.scalars().first()
                if existing:
                    continue
                
                # 2. Add job via endpoint logic to trigger LLM analysis and matching
                job_in = JobCreate(
                    title=job_dict["title"],
                    company=job_dict["company"],
                    url=job_dict["url"],
                    description=job_dict["description"],
                    location=job_dict["location"],
                    work_mode=job_dict["work_mode"],
                    salary=job_dict["salary"]
                )
                
                await create_job(job_in, db)
                logger.info(f"Automatically ingested job: {job_dict['title']} - {job_dict['company']}")
            except Exception as e:
                logger.error(f"Failed to ingest scraped job {job_dict.get('title')}: {e}")
                continue


scraper_service = ScraperService()

import asyncio
import sys
import logging
import urllib.parse
from typing import List, Dict, Callable, Any, Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models.db_models import Job, Application
from app.schemas.schemas import JobCreate

import re

logger = logging.getLogger("job_hunter.scraper_service")


def clean_job_title(title: str) -> str:
    """Removes platform noise tags like 'Em Alta', 'Vaga de', 'Nova', etc."""
    if not title:
        return ""
    # Strip prefixes like 'Vaga de', 'Vaga para', 'Vaga '
    t = re.sub(r'^(?:vaga\s+de\s+|vaga\s+para\s+|vaga\s+)', '', title, flags=re.IGNORECASE).strip()
    # Strip suffixes like 'Em Alta', 'Em Destaque', 'Nova', 'Urgente'
    t = re.sub(r'(?:Em\s+Alta|Em\s+Destaque|Nova|Urgente|Exclusiva)$', '', t, flags=re.IGNORECASE).strip()
    # Strip leading/trailing punctuation and collapse multiple spaces
    t = re.sub(r'\s+', ' ', t).strip(' -–—|:')
    return t


def is_unrelated_non_tech_title(title: str) -> bool:
    """Checks if a job title belongs to non-tech, operational, or administrative fields."""
    if not title:
        return False
    t = f" {title.lower()} "
    non_tech_patterns = [
        r"\bauxiliar\s+administrativ[oa]\b",
        r"\bassendente\b",
        r"\batendente\b",
        r"\brecepcionista\b",
        r"\bsecret[aá]ri[ao]\b",
        r"\bt[eé]cnico\s+em\s+eletr[oô]nica\b",
        r"\bt[eé]cnico\s+em\s+refrigera[cç][aã]o\b",
        r"\bt[eé]cnico\s+mec[aâ]nico\b",
        r"\bt[eé]cnico\s+de\s+manuten[cç][aã]o\b",
        r"\bt[eé]cnico\s+em\s+enfermagem\b",
        r"\bt[eé]cnico\s+de\s+seguran[cç]a\b",
        r"\bmec[aâ]nico\s+de\s+refrigera[cç][aã]o\b",
        r"\beletricista\b",
        r"\bmec[aâ]nico\b",
        r"\bmotorista\b",
        r"\bporteiro\b",
        r"\bvigilante\b",
        r"\bseguran[cç]a\b",
        r"\bauxiliar\s+de\s+limpeza\b",
        r"\bauxiliar\s+de\s+servi[cç]os\s+gerais\b",
        r"\bservi[cç]os\s+gerais\b",
        r"\bcopeir[ao]\b",
        r"\bcozinheir[ao]\b",
        r"\boperador\s+de\s+caixa\b",
        r"\bbalconista\b",
        r"\bvendedor[a]?\b",
        r"\bpromotor[a]?\s+de\s+vendas\b",
        r"\bestoquista\b",
        r"\balmoxarife\b",
        r"\bconferente\b",
        r"\bauxiliar\s+de\s+produ[cç][aã]o\b",
        r"\bgar[cç]om\b",
        r"\bgar[cç]onete\b",
        r"\bfarmac[eê]utic[ao]\b"
    ]
    for pattern in non_tech_patterns:
        if re.search(pattern, t, re.IGNORECASE):
            return True
    return False


def is_role_relevant(title: str, target_keyword: str) -> bool:
    """Checks if a job title is relevant to tech/software roles or target search keyword."""
    if not title:
        return False
    if is_unrelated_non_tech_title(title):
        return False

    t_clean = clean_job_title(title).lower()
    kw_clean = (target_keyword or "").lower().strip()

    # Core tech tokens indicating a tech/developer/data role
    tech_tokens = [
        "desenvolvedor", "developer", "dev", "programador", "software",
        "engenheiro", "engineer", "frontend", "front-end", "backend", "back-end",
        "fullstack", "full-stack", "full stack", "python", "javascript", "typescript",
        "react", "node", "java", "golang", "c#", ".net", "php", "ruby", "rust",
        "dados", "data", "analista", "bi", "sql", "ia", "ai", "machine learning",
        "nlp", "cloud", "aws", "azure", "gcp", "devops", "qa", "tester", "computação",
        "tecnologia", "ti", "it", "web", "mobile", "android", "ios", "flutter"
    ]

    kw_tokens = [w for w in re.split(r'[\s,;/]+', kw_clean) if len(w) > 2 and w not in ("vaga", "para", "com", "vagas", "junior", "pleno", "senior")]
    has_kw_match = any(token in t_clean for token in kw_tokens) if kw_tokens else True
    has_tech_token = any(token in t_clean for token in tech_tokens)

    return has_kw_match or has_tech_token


def is_senior_title(title: str) -> bool:
    """Checks if a job title indicates a Senior, Lead, Staff, Principal, or Management role."""
    if not title:
        return False
    t = f" {title.lower()} "
    patterns = [
        r"\bs[êe]nior\b",
        r"\bsr\.?\b",
        r"\biii\b",
        r"\biv\b",
        r"\bv\b",
        r"\btech\s+lead\b",
        r"\blead\b",
        r"\bstaff\b",
        r"\bprincipal\b",
        r"\bespecialista\b",
        r"\bexpert\b",
        r"\bhead\b",
        r"\bdiretor\b",
        r"\bdirector\b",
        r"\bgerente\b",
        r"\bmanager\b",
        r"\bcoordenador\b",
        r"\bcoordinator\b",
        r"\barquiteto\b",
        r"\barchitect\b"
    ]
    for pattern in patterns:
        if re.search(pattern, t, re.IGNORECASE):
            return True
    return False


def resolve_exclude_senior(seniority: Optional[str] = None, profile_seniority: Optional[str] = None, explicit_exclude: bool = True) -> bool:
    """Determines deterministically whether to exclude Senior/Lead roles based on search request and candidate profile."""
    if not explicit_exclude:
        return False

    # Check search request param
    if seniority:
        s_norm = seniority.strip().lower()
        if s_norm in ["senior", "sênior", "all", "todas"]:
            return False
        if any(w in s_norm for w in ["junior", "jr", "pleno", "estagio", "estágio"]):
            return True

    # Check candidate profile seniority
    if profile_seniority:
        p_norm = profile_seniority.strip().lower()
        if "senior" in p_norm or "sênior" in p_norm:
            # If strictly senior and not junior/pleno
            if not any(w in p_norm for w in ["junior", "jr", "pleno", "estagio", "estágio"]):
                return False

    # Default for Junior/Pleno/Entry or unclassified candidates: Exclude Senior
    return True


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
    async def scrape_linkedin_jobs(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes public LinkedIn job posts using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_linkedin_impl, keyword, location, limit, exclude_senior)

    async def _scrape_linkedin_impl(self, keyword: str, location: str, limit: int, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        kw_encoded = urllib.parse.quote(keyword)
        loc_encoded = urllib.parse.quote(location)
        url = f"https://www.linkedin.com/jobs/search?keywords={kw_encoded}&location={loc_encoded}&f_TPR=r604800&position=1&pageNum=0"
        logger.info(f"Scraping LinkedIn: {url} (exclude_senior={exclude_senior})")

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
                    if exclude_senior and is_senior_title(job_title):
                        logger.info(f"Skipping Senior LinkedIn job: '{job_title}'")
                        continue

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

    async def scrape_programathor_jobs(self, keyword: str, limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes jobs from Programathor (Brazilian Tech Job Board) using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_programathor_impl, keyword, limit, exclude_senior)

    async def _scrape_programathor_impl(self, keyword: str, limit: int, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        kw_encoded = urllib.parse.quote(keyword)
        url = f"https://programathor.com.br/jobs?text={kw_encoded}"
        logger.info(f"Scraping Programathor: {url} (exclude_senior={exclude_senior})")

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
                        
                    title = title_el.get_text().strip()
                    if exclude_senior and is_senior_title(title):
                        logger.info(f"Skipping Senior Programathor job: '{title}'")
                        continue

                    job_url = "https://programathor.com.br" + link_el["href"]
                    
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

    async def scrape_gupy_jobs(self, keyword: str, limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes jobs from Gupy Portal search engine using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_gupy_impl, keyword, limit, exclude_senior)

    async def _scrape_gupy_impl(self, keyword: str, limit: int, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        kw_encoded = urllib.parse.quote(keyword)
        url = f"https://portal.gupy.io/job-search/term={kw_encoded}"
        logger.info(f"Scraping Gupy Portal: {url} (exclude_senior={exclude_senior})")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="networkidle")
                
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                links = soup.find_all("a", href=True)
                job_links = [a for a in links if "/job/" in a["href"]]
                logger.info(f"Found {len(job_links)} Gupy job links.")
                
                count = 0
                for a in job_links:
                    if count >= limit:
                        break
                    
                    job_url = a["href"]
                    text_parts = [p_text.strip() for p_text in a.get_text(separator="|", strip=True).split("|") if p_text.strip()]
                    company = text_parts[0] if len(text_parts) > 0 else "Empresa Confidencial"
                    title = text_parts[1] if len(text_parts) > 1 else "Vaga Gupy"
                    
                    if exclude_senior and is_senior_title(title):
                        logger.info(f"Skipping Senior Gupy job: '{title}'")
                        continue
                    
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

    # ---------------- Indeed Scraper ----------------
    async def scrape_indeed_jobs(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes jobs from Indeed Brasil using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_indeed_impl, keyword, location, limit, exclude_senior)

    async def _scrape_indeed_impl(self, keyword: str, location: str, limit: int, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        kw_encoded = urllib.parse.quote(keyword)
        loc_encoded = urllib.parse.quote(location)
        url = f"https://br.indeed.com/jobs?q={kw_encoded}&l={loc_encoded}"
        logger.info(f"Scraping Indeed: {url} (exclude_senior={exclude_senior})")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select(".job_seen_beacon, [data-jk]")
                
                count = 0
                for card in cards:
                    if count >= limit:
                        break
                    
                    title_el = card.select_one(".jobTitle a, [data-jk], h2.jobTitle span, a[id*='job_']")
                    company_el = card.select_one('[data-testid="company-name"], .companyName')
                    loc_el = card.select_one('[data-testid="text-location"], .companyLocation')
                    snippet_el = card.select_one('.job-snippet, [class*="snippet"], .underShelfFooter')

                    if not title_el:
                        continue

                    title = title_el.get_text(strip=True)
                    if not title:
                        continue

                    if exclude_senior and is_senior_title(title):
                        logger.info(f"Skipping Senior Indeed job: '{title}'")
                        continue

                    job_key = card.get("data-jk") or (title_el.get("data-jk") if title_el else None)
                    if not job_key:
                        a_tag = card.select_one("a[href*='/rc/clk'], a[href*='/viewjob'], a[id*='job_']")
                        if a_tag and "href" in a_tag.attrs:
                            link = "https://br.indeed.com" + a_tag["href"]
                        else:
                            continue
                    else:
                        link = f"https://br.indeed.com/viewjob?jk={job_key}"

                    company = company_el.get_text(strip=True) if company_el else "Empresa Confidencial"
                    loc_text = loc_el.get_text(strip=True) if loc_el else location
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    desc = f"Vaga de {title} na empresa {company}. Localização: {loc_text}. {snippet}"
                    work_mode = "Remote" if "remoto" in desc.lower() or "remote" in desc.lower() else "Hybrid" if "hibrid" in desc.lower() or "híbrid" in desc.lower() else "On-site"

                    jobs_scraped.append({
                        "title": title,
                        "company": company,
                        "url": link,
                        "description": desc,
                        "location": loc_text,
                        "work_mode": work_mode,
                        "salary": "N/A"
                    })
                    count += 1
            except Exception as e:
                logger.error(f"Error scraping Indeed: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    # ---------------- InfoJobs Scraper ----------------
    async def scrape_infojobs_jobs(self, keyword: str, limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes jobs from InfoJobs Brasil using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_infojobs_impl, keyword, limit, exclude_senior)

    async def _scrape_infojobs_impl(self, keyword: str, limit: int, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        kw_encoded = urllib.parse.quote(keyword)
        url = f"https://www.infojobs.com.br/empregos.aspx?palabra={kw_encoded}"
        logger.info(f"Scraping InfoJobs: {url} (exclude_senior={exclude_senior})")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select(".element-vaga, [data-id], .js_vacancyRow, [class*='js_vacancy']")
                
                count = 0
                for card in cards:
                    if count >= limit:
                        break

                    title_el = card.select_one("a.text-decoration-none, .h2, h2, a[href*='/vaga-de-']")
                    company_el = card.select_one(".text-body, [class*='company'], .font-weight-bold, a[href*='/empresa-']")
                    loc_el = card.select_one(".text-medium, [class*='location'], .text-muted")
                    desc_el = card.select_one(".text-medium, p, [class*='description']")

                    if not title_el:
                        continue

                    title_raw = title_el.get_text(strip=True)
                    title = clean_job_title(title_raw)
                    if not title or is_unrelated_non_tech_title(title) or not is_role_relevant(title, keyword):
                        continue

                    if exclude_senior and is_senior_title(title):
                        logger.info(f"Skipping Senior InfoJobs job: '{title}'")
                        continue

                    link = title_el["href"] if "href" in title_el.attrs else ""
                    if link and not link.startswith("http"):
                        link = "https://www.infojobs.com.br" + link
                    if not link:
                        continue

                    company = company_el.get_text(strip=True) if company_el else "Empresa Confidencial"
                    loc_text = loc_el.get_text(strip=True) if loc_el else "Brasil"
                    desc_text = desc_el.get_text(strip=True) if desc_el else ""
                    desc = f"Vaga de {title} na empresa {company}. Localização: {loc_text}. {desc_text}"
                    work_mode = "Remote" if "remoto" in desc.lower() or "remote" in desc.lower() else "Hybrid" if "hibrid" in desc.lower() or "híbrid" in desc.lower() else "On-site"

                    jobs_scraped.append({
                        "title": title,
                        "company": company,
                        "url": link,
                        "description": desc,
                        "location": loc_text,
                        "work_mode": work_mode,
                        "salary": "N/A"
                    })
                    count += 1
            except Exception as e:
                logger.error(f"Error scraping InfoJobs: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    # ---------------- Trabalha Brasil Scraper ----------------
    async def scrape_trabalhabrasil_jobs(self, keyword: str, limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes jobs from Trabalha Brasil using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_trabalhabrasil_impl, keyword, limit, exclude_senior)

    async def _scrape_trabalhabrasil_impl(self, keyword: str, limit: int, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        kw_encoded = urllib.parse.quote(keyword)
        url = f"https://www.trabalhabrasil.com.br/vagas-de-emprego?sp={kw_encoded}"
        logger.info(f"Scraping Trabalha Brasil: {url} (exclude_senior={exclude_senior})")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                await page.goto(url, timeout=30000)
                await page.wait_for_timeout(3000)
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select('#jobs-wrapper .job__vacancy, .jg__job, [class*="job-card"]:not([class*="skeleton"]), article[class*="job"]')
                if not cards:
                    cards = soup.select('a[href*="/vagas-de-emprego-em-"]')

                count = 0
                for card in cards:
                    if count >= limit:
                        break

                    title_el = card.select_one('[class*="title"], h2, h3')
                    company_el = card.select_one('[class*="company"], [class*="enterprise"]')
                    loc_el = card.select_one('[class*="location"], [class*="city"]')
                    link_el = card if (card.name == "a" and card.get("href")) else card.select_one("a[href]")

                    title_raw = title_el.get_text(strip=True) if title_el else card.get_text(strip=True).split("\n")[0]
                    title = clean_job_title(title_raw)
                    if not title or is_unrelated_non_tech_title(title) or not is_role_relevant(title, keyword):
                        continue

                    if exclude_senior and is_senior_title(title):
                        logger.info(f"Skipping Senior Trabalha Brasil job: '{title}'")
                        continue

                    link = link_el["href"] if (link_el and "href" in link_el.attrs) else ""
                    if link and not link.startswith("http"):
                        link = "https://www.trabalhabrasil.com.br" + link
                    if not link:
                        continue

                    company = company_el.get_text(strip=True) if company_el else "Empresa Confidencial"
                    loc_text = loc_el.get_text(strip=True) if loc_el else "Brasil"
                    card_raw = card.get_text(separator=" | ", strip=True)
                    desc = f"Vaga de {title} na empresa {company}. Localização: {loc_text}. {card_raw}"
                    work_mode = "Remote" if "remoto" in desc.lower() or "remote" in desc.lower() else "Hybrid" if "hibrid" in desc.lower() or "híbrid" in desc.lower() else "On-site"

                    jobs_scraped.append({
                        "title": title,
                        "company": company,
                        "url": link,
                        "description": desc,
                        "location": loc_text,
                        "work_mode": work_mode,
                        "salary": "N/A"
                    })
                    count += 1
            except Exception as e:
                logger.error(f"Error scraping Trabalha Brasil: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    async def ingest_new_jobs(self, db: AsyncSession, scraped_jobs: List[Dict], exclude_senior: bool = False):
        """Saves scraped jobs to the database immediately with on-demand ATS evaluation upon user interaction."""
        for job_dict in scraped_jobs:
            try:
                title = clean_job_title(job_dict.get("title", ""))
                job_dict["title"] = title

                if not title or is_unrelated_non_tech_title(title):
                    logger.info(f"Ingestion Non-Tech Gate: blocked non-tech job '{title}'")
                    continue

                if exclude_senior and is_senior_title(title):
                    logger.info(f"Ingestion Seniority Gate: blocked Senior job '{title}'")
                    continue

                # 1. Skip if already processed URL
                if job_dict.get("url"):
                    result = await db.execute(select(Job).where(Job.url == job_dict["url"]))
                    existing = result.scalars().first()
                    if existing:
                        continue
                
                # 2. Add job record immediately
                job = Job(
                    title=title,
                    company=job_dict["company"],
                    url=job_dict.get("url"),
                    description=job_dict["description"],
                    location=job_dict.get("location"),
                    work_mode=job_dict.get("work_mode"),
                    salary=job_dict.get("salary")
                )
                db.add(job)
                await db.flush()

                # 3. Initial placeholder application for status tracking (DISCOVERED, on-demand ATS)
                init_app = Application(
                    job_id=job.id,
                    status="DISCOVERED"
                )
                db.add(init_app)
                await db.commit()

                logger.info(f"Ingested job: {job.title} - {job.company} (saved, ATS on-demand)")
            except Exception as e:
                logger.error(f"Failed to ingest scraped job {job_dict.get('title')}: {e}")
                await db.rollback()
                continue


scraper_service = ScraperService()

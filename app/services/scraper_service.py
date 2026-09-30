import asyncio
import sys
import logging
import urllib.parse
from typing import List, Dict, Callable, Any, Optional
import re
import unicodedata

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from app.config import settings
from app.models.db_models import Job, Application
from app.schemas.schemas import JobCreate

logger = logging.getLogger("job_hunter.scraper_service")


def slugify(text: str) -> str:
    """Converts a text to a URL-safe slug."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = re.sub(r'[^\w\s-]', '', text.lower())
    return re.sub(r'[-\s]+', '-', text).strip('-')


def canonicalize_job_url(url: str) -> str:
    """
    Strips tracking query parameters (utm, ref, position, pageNum, etc.) and trailing slashes to prevent duplicate ingestion.
    """
    if not url or url.startswith('javascript:'):
        return ""
    try:
        p = urllib.parse.urlparse(url.strip())
        clean_params = []
        if p.query:
            for q in p.query.split('&'):
                if not any(q.lower().startswith(prefix) for prefix in [
                    'utm_', 'ref', 'position', 'pagenum', 'trackingid', 'trk', 'midtoken', 'start', 'from', 'f_tpr', 'page'
                ]):
                    clean_params.append(q)
        new_query = '&'.join(clean_params)
        clean_path = p.path.rstrip('/')
        return urllib.parse.urlunparse((p.scheme, p.netloc, clean_path, '', new_query, ''))
    except Exception:
        return url.strip()


def compute_job_fingerprint(title: str, company: str) -> str:
    """Computes a normalized title + company fingerprint for cross-platform deduplication."""
    t_norm = re.sub(r'[^a-zA-Z0-9]', '', (title or '').lower())
    c_norm = re.sub(r'[^a-zA-Z0-9]', '', (company or '').lower())
    if c_norm in ('', 'empresaconfidencial', 'confidencial', 'anonimo', 'anônimo'):
        return f"title_{t_norm}"
    return f"{t_norm}___{c_norm}"


def parse_location(location: str) -> Dict[str, Any]:
    """
    Parses and cleanly separates location into city, state, clean location string and slug.
    Guarantees that location is never concatenated into keyword fields.
    """
    if not location or location.strip().lower() in (
        'brasil', 'brazil', 'todo brasil', 'nacional', 'não informado', 'nao informado', 'n/a', 'none', 'unknown', ''
    ):
        return {'is_national': True, 'city': '', 'state': '', 'clean_loc': 'Brasil', 'slug': ''}

    loc = location.strip()
    m = re.match(r'^([^,-]+)[,-]\s*([A-Za-z]{2})$', loc)
    if m:
        city = m.group(1).strip()
        state = m.group(2).strip().upper()
        return {'is_national': False, 'city': city, 'state': state, 'clean_loc': f'{city}, {state}', 'slug': slugify(f'{city}-{state}')}
    return {'is_national': False, 'city': loc, 'state': '', 'clean_loc': loc, 'slug': slugify(loc)}


INFOJOBS_POBLACION_CACHE: Dict[str, str] = {
    "salvador": "5202974",
    "feira de santana": "5202596",
    "sao paulo": "5211323",
    "são paulo": "5211323",
    "rio de janeiro": "5208466",
    "curitiba": "5207873",
    "belo horizonte": "5200877",
    "brasilia": "5201886",
    "brasília": "5201886",
    "porto alegre": "5208573",
    "recife": "5207869",
    "fortaleza": "5202868"
}


def get_infojobs_poblacion_id(location_query: str) -> str:
    """
    Resolves location string to official InfoJobs poblacion ID via autocomplete API.
    Caches results in-memory for instant 0ms subsequent queries.
    """
    if not location_query:
        return ""

    clean_q = location_query.strip().lower()
    if clean_q in INFOJOBS_POBLACION_CACHE:
        return INFOJOBS_POBLACION_CACHE[clean_q]

    # Query InfoJobs autocomplete API
    try:
        import urllib.request
        import json
        q_enc = urllib.parse.quote(location_query.strip())
        url = f"https://www.infojobs.com.br/mf-publicarea/api/autocompleteapi/locations?query={q_enc}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        })
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                suggestions = data.get("suggestions", [])
                for s in suggestions:
                    val = s.get("value", "").lower()
                    if clean_q in val or val in clean_q:
                        p_id = s.get("data", {}).get("id", "")
                        if p_id:
                            INFOJOBS_POBLACION_CACHE[clean_q] = str(p_id)
                            return str(p_id)
                if suggestions:
                    p_id = suggestions[0].get("data", {}).get("id", "")
                    if p_id:
                        INFOJOBS_POBLACION_CACHE[clean_q] = str(p_id)
                        return str(p_id)
    except Exception as e:
        logger.warning(f"Could not resolve InfoJobs poblacion ID for '{location_query}': {e}")

    return ""


EXPIRED_OR_CLOSED_PATTERNS = [
    r"\bvencid[ao]s?\b",
    r"\bencerrad[ao]s?\b",
    r"\bfinalizad[ao]s?\b",
    r"\bexpirad[ao]s?\b",
    r"\bpausad[ao]s?\b",
    r"\bfechad[ao]s?\b",
    r"\bprocesso\s+(?:seletivo\s+)?encerrado\b",
    r"\binscri[cç][oõ]es\s+encerradas\b",
    r"\bn[aã]o\s+est[aá]\s+mais\s+aceitando\s+candidaturas\b",
    r"\bn[aã]o\s+aceita\s+mais\s+candidaturas\b",
    r"\bvaga\s+fechada\b",
    r"\bvaga\s+encerrada\b",
    r"\bvaga\s+vencida\b",
    r"\bvaga\s+finalizada\b",
    r"\bno\s+longer\s+accepting\s+applications\b",
    r"\bjob\s+(?:is\s+)?closed\b",
    r"\bexpired\b"
]


def is_expired_or_closed_job(title: str, description: str = "", card_text: str = "") -> bool:
    """
    Detects if a job listing has expired, closed, or ceased accepting applications.
    Checks title prefixes/tags, card text badges and description headers.
    """
    # 1. Check title first (e.g. 'Vencida Developer...', '[ENCERRADA] Fullstack')
    if title:
        t_low = f" {title.lower()} "
        if any(re.search(pat, t_low, re.IGNORECASE) for pat in EXPIRED_OR_CLOSED_PATTERNS):
            return True

    # 2. Check card/badge text if available (e.g. tag-expired, status badges)
    if card_text:
        c_low = f" {card_text.lower()} "
        if any(re.search(pat, c_low, re.IGNORECASE) for pat in EXPIRED_OR_CLOSED_PATTERNS):
            return True

    # 3. Check description header
    if description:
        d_head = f" {description[:400].lower()} "
        if any(re.search(pat, d_head, re.IGNORECASE) for pat in EXPIRED_OR_CLOSED_PATTERNS):
            return True

    return False


NON_TECH_PATTERNS = [
    # General labor / services / maintenance
    r"\bauxiliar\b", r"\bassendente\b", r"\batendente\b", r"\brecepcionista\b", r"\bsecret[aá]ri[ao]\b",
    r"\bt[eé]cnico\s+em\b", r"\bt[eé]cnico\s+de\b", r"\bt[eé]cnico\s+ambiental\b", r"\bt[eé]cnico\b",
    r"\beletricista\b", r"\bmec[aâ]nico\b", r"\bmotorista\b", r"\bporteiro\b", r"\bvigilante\b",
    r"\bseguran[cç]a\b", r"\blimpeza\b", r"\bservi[cç]os\s+gerais\b", r"\bcopeir[ao]\b", r"\bcozinheir[ao]\b",
    r"\bgar[cç][oõ]m\b", r"\bgar[cç]onete\b", r"\bpintor\b", r"\bmanuten[cç][aã]o\b", r"\bestoquista\b", r"\balmoxarife\b",
    r"\boperador\b", r"\btelefonista\b", r"\bcaixa\b", r"\bbalconista\b", r"\bconferente\b",
    # Commercial / Sales / Marketing non-tech
    r"\bvendedor\b", r"\bvendas\b", r"\bcomercial\b", r"\bcorretor\b", r"\btelemarketing\b", r"\bsdr\b", r"\bbdr\b",
    # Education / Academic
    r"\bprofessor\b", r"\bprofessora\b", r"\bbiologia\b", r"\bqu[ií]mica\b", r"\bf[ií]sica\b", r"\bmatem[aá]tica\b",
    r"\bpedagog[ao]\b", r"\bdocente\b", r"\beduca[cç][aã]o\s+f[ií]sica\b", r"\bgeografia\b", r"\bhist[oó]ria\b",
    # Legal / Admin / Finance / HR non-tech
    r"\blicita[cç][oõ]es\b", r"\blicita[cç][aã]o\b", r"\badvogad[oa]\b", r"\bjur[ií]dic[oa]\b", r"\bcont[aá]bil\b",
    r"\bcontador\b", r"\bfiscal\b", r"\bfinanceir[oa]\b", r"\brh\b", r"\brecursos\s+humanos\b", r"\bdp\b",
    r"\bdepartamento\s+pessoal\b", r"\bcomprador\b", r"\blog[ií]stica\b", r"\bcompras\b",
    # Healthcare
    r"\benfermeir[oa]\b", r"\benfermagem\b", r"\bm[eé]dic[oa]\b", r"\bdentista\b", r"\bpsic[oó]log[oa]\b",
    r"\bnutricionista\b", r"\bfarmac[eê]utic[oa]\b", r"\bfisioterapeuta\b",
    # Non-tech / Operational Analyst False Positives
    r"\banalista\s+de\s+(?:rm|totvs|erp|qualidade|esg|ehg|opera[cç][oõ]es|log[ií]stica|suprimentos|compras|fiscal|cont[aá]bil|financeir[oa]|cr[eé]dito|cobran[cç]a|rh|recursos\s+humanos|departamento\s+pessoal|dp|suporte|atendimento|p[oó]s[- ]vendas?|planejamento\s+financeiro|folha|frota|faturamento|sinistro|riscos?|laborat[oó]rio|compliance|processos?|facilities|manuten[cç][aã]o|farmac[eê]utic[oa]|qu[ií]mic[oa]|importa[cç][aã]o|exporta[cç][aã]o)\b"
]

DATA_KEYWORDS = [
    "dado", "dados", "data", "analytics", "bi", "business intelligence",
    "power bi", "powerbi", "sql", "tableau", "etl", "bigquery", "databricks",
    "looker", "machine learning", "estatística", "estatistica", "analytics engineer",
    "data analyst", "analista de dados", "data science", "ciência de dados", "cientista de dados",
    "engenheiro de dados", "engenharia de dados", "data engineer"
]

DEV_KEYWORDS = [
    "desenvolvedor", "desenvolvedora", "developer", "dev", "programador", "programadora",
    "software", "frontend", "front-end", "backend", "back-end", "fullstack", "full-stack", "full stack",
    "engenheiro de software", "engenheira de software", "software engineer",
    "python", "javascript", "typescript", "react", "node", "nodejs", "node.js", "java", "golang", "go",
    "c#", ".net", "dotnet", "php", "laravel", "ruby", "rails", "rust", "c++", "kotlin", "swift", "flutter",
    "mobile", "web developer"
]

CORE_TECH_KEYWORDS = list(set(DATA_KEYWORDS + DEV_KEYWORDS + [
    "devops", "cloud engineer", "cloud", "qa", "quality assurance", "tester", "sre"
]))


def clean_job_title(title: str) -> str:
    """Removes platform noise tags like 'Em Alta', 'Vaga de', 'Nova', ID tokens, etc."""
    if not title:
        return ""
    # Strip prefix boilerplate
    t = re.sub(r'^(?:vaga\s+de\s+|vaga\s+para\s+|vaga\s+|\d+\s+vagas\s+de\s+)', '', title, flags=re.IGNORECASE).strip()
    # Strip suffix tags
    t = re.sub(r'(?:Em\s+Alta|Em\s+Destaque|Nova|Urgente|Exclusiva|Copiar ID.*)$', '', t, flags=re.IGNORECASE).strip()
    # Strip trailing boilerplate ID codes
    t = re.sub(r'\d{6,}.*$', '', t).strip()
    # Collapse multiple spaces and trim
    t = re.sub(r'\s+', ' ', t).strip(' -–—|:')
    return t


def is_unrelated_non_tech_title(title: str) -> bool:
    """Checks if a job title belongs to non-tech, operational, or administrative fields."""
    if not title:
        return True
    t = f" {title.lower()} "
    return any(re.search(pat, t, re.IGNORECASE) for pat in NON_TECH_PATTERNS)


def is_role_relevant(title: str, target_keyword: str = "") -> bool:
    """Strictly validates if a title belongs to authentic software, developer, data or tech roles matching target domain."""
    if not title or len(title.strip()) < 3:
        return False

    t_clean = clean_job_title(title).lower()
    kw_norm = target_keyword.strip().lower() if target_keyword else ""

    # 1. Block platform noise artifacts
    if "copiar id" in t_clean or re.match(r"^[a-z0-9\s\-_/|]{1,6}$", t_clean.strip()):
        return False

    # 2. Block non-tech and operational analyst patterns
    if is_unrelated_non_tech_title(title):
        return False

    # 3. Domain & Specialization Alignment based on target_keyword
    if kw_norm:
        # A. Target is DATA / ANALYTICS
        if any(dk in kw_norm for dk in ["dado", "dados", "data", "analytics", "bi", "power bi", "sql"]):
            return any(re.search(r'\b' + re.escape(dk) + r'\b', t_clean) for dk in DATA_KEYWORDS)

        # B. Target is QA / TESTING
        if any(qak in kw_norm for qak in ["qa", "tester", "quality assurance", "testes"]):
            return any(re.search(r'\b' + re.escape(qak) + r'\b', t_clean) for qak in ["qa", "quality assurance", "tester", "testes", "automação de testes"])

        # C. Target is DEVOPS / CLOUD
        if any(dvk in kw_norm for dvk in ["devops", "cloud", "sre", "infraestrutura"]):
            return any(re.search(r'\b' + re.escape(dvk) + r'\b', t_clean) for dvk in ["devops", "cloud", "sre", "infraestrutura", "kubernetes", "aws", "azure", "gcp"])

        # D. Target is ANALYST / IT / SUPPORT / SYSTEMS / INFRA
        if any(ak in kw_norm for ak in ["suporte", "helpdesk", "service desk", "infra", "sistemas", "ti", "it analyst", "it support", "técnico de ti", "analista de ti", "analista de suporte", "analista de infra"]) or ("analista" in kw_norm and not any(dk in kw_norm for dk in ["desenvolvedor", "developer", "programador", "software"])):
            is_it_analyst = any(re.search(r'\b' + re.escape(k) + r'\b', t_clean) for k in [
                "analista", "suporte", "ti", "it", "sistemas", "infraestrutura", "redes", "helpdesk", "service desk", "banco de dados", "dba", "segurança", "infra"
            ])
            is_pure_dev = any(re.search(r'\b' + re.escape(k) + r'\b', t_clean) for k in [
                "desenvolvedor", "desenvolvedora", "developer", "dev", "programador", "software engineer", "full stack", "fullstack"
            ]) and not any(k in t_clean for k in ["analista de sistemas", "analista de ti", "analista de suporte", "analista desenvolvedor"])
            if is_pure_dev or not is_it_analyst:
                return False
            return True

        # E. Target is BACKEND
        if any(bk in kw_norm for bk in ["backend", "back-end", "back end"]):
            is_backend = any(re.search(r'\b' + re.escape(k) + r'\b', t_clean) for k in [
                "backend", "back-end", "back end", "fullstack", "full-stack", "full stack",
                "python", "node", "nodejs", "node.js", "java", "php", "laravel", "c#", ".net", "dotnet", "golang", "go", "ruby", "rust"
            ])
            is_pure_frontend = any(re.search(r'\b' + re.escape(k) + r'\b', t_clean) for k in ["frontend", "front-end", "front end", "ui/ux", "designer"]) and not any(k in t_clean for k in ["backend", "fullstack", "full stack"])
            is_pure_mobile = any(re.search(r'\b' + re.escape(k) + r'\b', t_clean) for k in ["flutter", "react native", "ios", "android"]) and not any(k in t_clean for k in ["backend", "fullstack", "full stack"])
            if is_pure_frontend or is_pure_mobile or not is_backend:
                return False
            return True

        # F. Target is FRONTEND
        if any(fk in kw_norm for fk in ["frontend", "front-end", "front end"]):
            is_frontend = any(re.search(r'\b' + re.escape(k) + r'\b', t_clean) for k in [
                "frontend", "front-end", "front end", "fullstack", "full-stack", "full stack",
                "react", "vue", "angular", "javascript", "typescript", "web", "html", "css"
            ])
            is_pure_backend = any(re.search(r'\b' + re.escape(k) + r'\b', t_clean) for k in ["backend", "back-end", "dba", "devops"]) and not any(k in t_clean for k in ["frontend", "fullstack", "full stack"])
            if is_pure_backend or not is_frontend:
                return False
            return True

        # G. Target is FULLSTACK
        if any(fsk in kw_norm for fsk in ["fullstack", "full-stack", "full stack"]):
            return any(re.search(r'\b' + re.escape(k) + r'\b', t_clean) for k in [
                "fullstack", "full-stack", "full stack", "desenvolvedor", "developer", "programador",
                "software", "web", "react", "node", "python", "php", "javascript", "typescript"
            ])

        # H. Target is SPECIFIC LANGUAGE/TECH (Python, React, Node, PHP, Java, etc.)
        target_tokens = [w for w in re.split(r'[\s,;/]+', kw_norm) if len(w) > 2 and w not in ("desenvolvedor", "developer", "dev", "programador", "vaga", "junior", "pleno", "senior", "jr", "sr")]
        if target_tokens:
            has_token_match = any(re.search(r'\b' + re.escape(tk) + r'\b', t_clean) for tk in target_tokens)
            if has_token_match:
                return True
            return False

        # If keyword was provided but did not match specialized rules above, strictly reject
        return False

    # Fallback when NO target keyword was provided: check if title contains any known tech keyword
    has_tech_kw = any(re.search(r'\b' + re.escape(tk) + r'\b', t_clean) for tk in CORE_TECH_KEYWORDS)
    if not has_tech_kw:
        has_tech_kw = any(tk in t_clean for tk in [
            "full stack", "full-stack", "front-end", "back-end",
            "desenvolvedor", "developer", "programador", "software", "sistemas"
        ])

    return bool(has_tech_kw)


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


def is_location_matching(
    job_location: str,
    target_location: str = "Brasil",
    work_mode: str = "On-site",
    description: str = ""
) -> bool:
    """Strictly validates if a scraped job matches the targeted location.
    
    - Generic target ('Brasil'): Accepts all Brazilian locations and Remote. Discards foreign locations.
    - Strict Remote target ('Remoto'): Accepts ONLY Remote/Home Office jobs.
    - City/State target ('Salvador, BA', 'São Paulo', etc.):
      - Always accepts Remote jobs (can be worked from target city).
      - Accepts On-site/Hybrid ONLY if located in the target city/state.
      - Discards On-site/Hybrid jobs from other cities/states (e.g. On-site in SP when searching Salvador).
    """
    if not target_location or not target_location.strip():
        return True

    t_norm = target_location.strip().lower()
    j_norm = (job_location or "").strip().lower()
    desc_norm = (description or "").lower()
    w_norm = (work_mode or "").lower()

    # 1. Generic national target
    if t_norm in ("brasil", "brazil", "todo brasil", "nacional", "all"):
        FOREIGN_LOCATIONS = {"united states", "usa", "uk", "united kingdom", "india", "germany", "deutschland", "canada", "poland", "argentina", "colombia", "mexico", "chile", "france", "australia", "spain", "london", "bangalore", "berlin", "lisboa", "porto, portugal"}
        if any(f in j_norm for f in FOREIGN_LOCATIONS) and not ("brasil" in j_norm or "brazil" in j_norm or "remoto" in j_norm):
            return False
        return True

    # 2. Strict Remote target
    if t_norm in ("remoto", "remote", "home office", "home-office", "teletrabalho"):
        return w_norm == "remote" or "remoto" in j_norm or "home office" in j_norm or "100% remoto" in desc_norm

    # 3. Specific City / State Target (e.g. "Salvador, BA", "São Paulo", "Curitiba")
    # Remote jobs are always valid from any location
    if w_norm == "remote" or "remoto" in j_norm or "home office" in j_norm:
        return True

    # Tokenize target into words
    target_tokens = [tok.strip() for tok in re.split(r"[,/\-\s]+", t_norm) if len(tok.strip()) >= 2]
    
    STATE_MAPPING = {
        "ba": "bahia", "bahia": "ba",
        "sp": "são paulo", "sao paulo": "sp",
        "rj": "rio de janeiro", "rio": "rj",
        "mg": "minas gerais", "minas": "mg",
        "pr": "paraná", "parana": "pr",
        "sc": "santa catarina",
        "rs": "rio grande do sul",
        "pe": "pernambuco",
        "ce": "ceará", "ceara": "ce",
        "df": "distrito federal", "brasília": "df", "brasilia": "df",
        "go": "goiás", "goias": "go",
        "es": "espírito santo", "espirito santo": "es"
    }
    
    expanded_target = set(target_tokens)
    for tok in list(target_tokens):
        if tok in STATE_MAPPING:
            expanded_target.add(STATE_MAPPING[tok])

    # Check word boundaries for tokens in job_location
    for tok in expanded_target:
        if len(tok) <= 2:
            if re.search(r'\b' + re.escape(tok) + r'\b', j_norm):
                return True
        else:
            if tok in j_norm:
                return True

    # Check description for full city/state name
    for tok in target_tokens:
        if len(tok) > 3 and tok in desc_norm:
            return True

    logger.info(f"Filtering out On-site/Hybrid job in '{job_location}' (Target was '{target_location}')")
    return False


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
            res = coro_fn(*args, **kwargs)
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                return loop.run_until_complete(res)
            return res
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
        loc_clean = location.strip() if location and location.strip() else "Brasil"
        loc_lower = loc_clean.lower()
        
        # Determine strict LinkedIn location filtering
        if loc_lower in ("brasil", "brazil", "todo brasil", "nacional"):
            url = f"https://br.linkedin.com/jobs/search?keywords={kw_encoded}&location=Brasil&geoId=106057199&f_TPR=r604800&position=1&pageNum=0"
        elif loc_lower in ("remoto", "remote", "home office", "home-office"):
            url = f"https://br.linkedin.com/jobs/search?keywords={kw_encoded}&location=Brasil&geoId=106057199&f_WT=2&f_TPR=r604800&position=1&pageNum=0"
        else:
            loc_encoded = urllib.parse.quote(loc_clean)
            url = f"https://br.linkedin.com/jobs/search?keywords={kw_encoded}&location={loc_encoded}&f_TPR=r604800&position=1&pageNum=0"

        logger.info(f"Scraping LinkedIn Jobs ({loc_clean}): {url} (exclude_senior={exclude_senior})")

        from app.services.linkedin_auth import get_linkedin_storage_state_path
        state_file = get_linkedin_storage_state_path()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context_kwargs: Dict[str, Any] = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "locale": "pt-BR",
                "extra_http_headers": {"Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"}
            }
            if state_file:
                logger.info(f"Using authenticated LinkedIn persistent session: {state_file}")
                context_kwargs["storage_state"] = str(state_file)

            context = await browser.new_context(**context_kwargs)
            if settings.LINKEDIN_COOKIE_LI_AT and not state_file:
                await context.add_cookies([{
                    "name": "li_at",
                    "value": settings.LINKEDIN_COOKIE_LI_AT,
                    "domain": ".www.linkedin.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True
                }])
            page = await context.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                
                # Dynamic scrolling based on limit (up to 50 jobs)
                scroll_count = min(12, max(2, (limit // 4) + 1))
                for _ in range(scroll_count):
                    await page.mouse.wheel(0, 1000)
                    await asyncio.sleep(0.8)
                    try:
                        more_btn = page.locator("button.infinite-scroller__show-more-button, button[aria-label*='mais vagas']")
                        if await more_btn.is_visible():
                            await more_btn.click()
                            await asyncio.sleep(1)
                    except Exception:
                        pass

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
                    if not is_role_relevant(job_title, keyword):
                        logger.info(f"Skipping non-relevant LinkedIn job: '{job_title}' (Target was '{keyword}')")
                        continue

                    if exclude_senior and is_senior_title(job_title):
                        logger.info(f"Skipping Senior LinkedIn job: '{job_title}'")
                        continue

                    loc_text = loc_el.get_text(strip=True) if loc_el else "Brasil"
                    
                    # Filter out international/foreign locations
                    FOREIGN_LOCATIONS = {"united states", "usa", "uk", "united kingdom", "india", "germany", "deutschland", "canada", "poland", "argentina", "colombia", "mexico", "chile", "france", "australia", "spain", "london", "bangalore", "berlin"}
                    loc_lower = loc_text.lower()
                    if any(f in loc_lower for f in FOREIGN_LOCATIONS) and not ("brasil" in loc_lower or "brazil" in loc_lower):
                        logger.info(f"Skipping foreign LinkedIn job location: '{loc_text}' for '{job_title}'")
                        continue

                    company = company_el.get_text().strip()
                    job_url = link_el["href"].split("?")[0]
                    loc_text = loc_el.get_text(strip=True) if loc_el else "Brasil"
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

                    # Strict location validation
                    if not is_location_matching(loc_text, location, work_mode, desc):
                        continue

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

    # ---------------- LinkedIn Posts / Feed Scraper ----------------
    async def scrape_linkedin_posts(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False, date_filter: str = "past-week") -> List[Dict]:
        """Scrapes hiring posts from LinkedIn content feed prioritizing past 24h to 1 week with location context."""
        return await _run_in_proactor_thread(self._scrape_linkedin_posts_impl, keyword, location, limit, exclude_senior, date_filter)

    async def _scrape_linkedin_posts_impl(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False, date_filter: str = "past-week") -> List[Dict]:
        jobs_scraped = []
        seen_urls = set()
        
        kw_clean = keyword.strip().strip('"')
        loc_clean = location.strip() if location and location.strip() else "Brasil"
        loc_lower = loc_clean.lower()
        
        # Build search queries prioritizing hiring keywords and location
        if loc_lower in ("brasil", "brazil", "todo brasil", "nacional"):
            queries_to_try = [
                f'vaga {kw_clean}',
                f'"{kw_clean}" (contratando OR vaga OR "estamos contratando" OR "envie seu cv" OR "mande seu cv")',
                f'{kw_clean} (contratando OR oportunidade OR "vaga aberta" OR "compartilhem")'
            ]
        elif loc_lower in ("remoto", "remote", "home office", "home-office"):
            queries_to_try = [
                f'vaga {kw_clean} (remoto OR "home office")',
                f'"{kw_clean}" (remoto OR "home office") (contratando OR vaga)',
                f'{kw_clean} ("100% remoto" OR "home office") ("estamos contratando" OR oportunidade)'
            ]
        else:
            queries_to_try = [
                f'vaga {kw_clean} "{loc_clean}"',
                f'"{kw_clean}" ("{loc_clean}" OR remoto) (contratando OR vaga)',
                f'{kw_clean} "{loc_clean}" (contratando OR oportunidade OR "vaga aberta")'
            ]

        from app.services.linkedin_auth import get_linkedin_storage_state_path
        state_file = get_linkedin_storage_state_path()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context_kwargs: Dict[str, Any] = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "locale": "pt-BR",
                "extra_http_headers": {"Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"}
            }
            if state_file:
                context_kwargs["storage_state"] = str(state_file)

            context = await browser.new_context(**context_kwargs)
            if settings.LINKEDIN_COOKIE_LI_AT and not state_file:
                await context.add_cookies([{
                    "name": "li_at",
                    "value": settings.LINKEDIN_COOKIE_LI_AT,
                    "domain": ".www.linkedin.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True
                }])
            
            page = await context.new_page()
            try:
                # Priority date filters: first 24h, then 1 week
                date_stages = ["past-24h", "past-week"] if date_filter in ("past-week", "past-24h") else [date_filter]

                for stage in date_stages:
                    if len(jobs_scraped) >= limit:
                        break

                    for q_text in queries_to_try:
                        if len(jobs_scraped) >= limit:
                            break

                        kw_encoded = urllib.parse.quote(q_text)
                        url = f"https://www.linkedin.com/search/results/content/?keywords={kw_encoded}&origin=SWITCH_SEARCH_VERTICAL&datePosted=%22{stage}%22&sortBy=%22date_posted%22"
                        logger.info(f"Scraping LinkedIn Posts [{stage}]: {url} (exclude_senior={exclude_senior})")

                        try:
                            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                            await page.wait_for_timeout(2500)

                            # Check if redirected to login wall
                            if "login" in page.url or "uas/login" in page.url or "checkpoint" in page.url:
                                logger.warning("LinkedIn post search requires active session. Connect LinkedIn via Dashboard.")
                                return jobs_scraped

                            # Expand "ver mais" on posts
                            try:
                                await page.evaluate("() => { document.querySelectorAll('button.feed-shared-inline-show-more-text__see-more-less-toggle, button.see-more').forEach(b => b.click()); }")
                            except Exception:
                                pass

                            # Scroll to load feed items dynamically up to limit
                            scroll_count = min(12, max(2, (limit // 3) + 1))
                            for _ in range(scroll_count):
                                await page.mouse.wheel(0, 1000)
                                await asyncio.sleep(0.7)

                            html = await page.content()
                            soup = BeautifulSoup(html, "html.parser")

                            # Parse post items using both Modern and Legacy LinkedIn selectors
                            cards_data = []

                            # 1. Modern DOM: Search for individual feed item markers
                            feed_markers = soup.find_all(lambda el: el.name in ('span', 'h2', 'div', 'p') and el.string and any(m_txt in el.string for m_txt in ['Publicação no feed', 'Feed post', 'Publicação']))
                            for m in feed_markers:
                                card_div = None
                                curr = m.parent
                                while curr and curr.name != 'main' and curr.name != 'body':
                                    contained = curr.find_all(lambda el: el.name in ('span', 'h2', 'div', 'p') and el.string and any(m_txt in el.string for m_txt in ['Publicação no feed', 'Feed post', 'Publicação']))
                                    if len(contained) == 1:
                                        card_div = curr
                                    elif len(contained) > 1:
                                        break
                                    curr = curr.parent
                                if card_div and card_div not in cards_data:
                                    cards_data.append(card_div)

                            # 2. Legacy DOM fallback
                            if not cards_data:
                                legacy_cards = soup.select(".feed-shared-update-v2, li.reusable-search__result-container, [data-urn*='activity'], .feed-shared-update-v2__content, div[data-id*='urn:li:activity']")
                                if not legacy_cards:
                                    legacy_cards = soup.select("[class*='feed-shared-update'], [class*='update-components-actor']")
                                cards_data = legacy_cards

                            logger.info(f"LinkedIn posts parser detected {len(cards_data)} card elements for query '{q_text}' [{stage}]")

                            for card in cards_data:
                                if len(jobs_scraped) >= limit:
                                    break

                                full_card_text = card.get_text(separator="\n", strip=True) if card else ""
                                if not full_card_text or len(full_card_text) < 40:
                                    continue

                                # 1. Skip candidate seeking posts (#OpenToWork, buscando emprego)
                                card_lower = full_card_text.lower()
                                if any(phrase in card_lower for phrase in ["#opentowork", "buscando oportunidade", "em busca de oportunidade", "em busca da minha primeira oportunidade", "buscando recolocação", "buscando emprego", "estou à procura"]):
                                    continue

                                # 2. Clean noise lines
                                raw_lines = [line.strip() for line in full_card_text.split("\n") if line.strip()]
                                clean_lines = []
                                for l in raw_lines:
                                    if any(skip in l.lower() for skip in ['publicação no feed', 'feed post', '• 1º', '• 2º', '• 3º', 'seguir', 'acesse meu site', 'conectar', 'gostei', 'comentar', 'compartilhar', 'enviar', 'visualizações', 'denunciar']):
                                        continue
                                    clean_lines.append(l)

                                clean_body = "\n".join(clean_lines)
                                if len(clean_body) < 30:
                                    continue

                                # 3. Extract author name
                                author = clean_lines[0] if clean_lines else "Recrutador LinkedIn"
                                author_link = card.find("a", href=lambda h: h and "/in/" in h)
                                if author_link:
                                    author_text = author_link.get_text(strip=True).split("•")[0].strip()
                                    if author_text and len(author_text) > 2:
                                        author = author_text

                                # 4. Extract external or post link
                                post_url = (author_link.get("href") if author_link else url) or url
                                ext_links = card.find_all("a", href=lambda h: h and ("/safety/go/" in h or "lnkd.in" in h or "/jobs/view/" in h or ("http" in h and "/in/" not in h and "linkedin.com/feed" not in h and "origin=HASH_TAG" not in h)))
                                if ext_links:
                                    raw_link = ext_links[-1].get("href", "")
                                    if "/safety/go/?url=" in raw_link:
                                        match_url = re.search(r"[?&]url=([^&]+)", raw_link)
                                        if match_url:
                                            post_url = urllib.parse.unquote(match_url.group(1))
                                    else:
                                        post_url = raw_link

                                if post_url in seen_urls:
                                    continue
                                seen_urls.add(post_url)

                                # 5. Email extraction
                                email_match = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", clean_body)
                                extracted_email = email_match.group(1) if email_match else None

                                # 6. Job title extraction from body lines
                                body_lines = clean_lines[2:] if len(clean_lines) > 2 else clean_lines
                                title_candidate = ""
                                for b_idx, b_line in enumerate(body_lines):
                                    if any(w in b_line.lower() for w in ['desenvolvedor', 'developer', 'analista', 'engineer', 'full stack', 'frontend', 'backend', 'vaga', 'oportunidade', 'contrat']):
                                        if not any(skip in b_line.lower() for skip in ['link', 'http', 'não tenho ligação', 'contato', 'há ']):
                                            if len(clean_job_title(b_line)) < 6 and b_idx + 1 < len(body_lines):
                                                title_candidate = f"{b_line} {body_lines[b_idx+1]}"
                                            else:
                                                title_candidate = b_line
                                            break
                                if not title_candidate:
                                    title_candidate = f"{kw_clean} (Post por {author})"

                                inferred_title = clean_job_title(title_candidate)
                                if not is_role_relevant(inferred_title, kw_clean):
                                    inferred_title = f"{kw_clean} (Post por {author})"

                                if exclude_senior and (is_senior_title(inferred_title) or is_senior_title(title_candidate)):
                                    logger.info(f"Skipping Senior LinkedIn post: '{inferred_title}'")
                                    continue

                                work_mode = "Remote" if ("remoto" in clean_body.lower() or "remote" in clean_body.lower() or "home office" in clean_body.lower()) else ("Hybrid" if ("hibrid" in clean_body.lower() or "híbrid" in clean_body.lower()) else "On-site")
                                
                                # Strict location validation for posts
                                if not is_location_matching("Brasil", location, work_mode, clean_body):
                                    continue

                                desc = f"Publicação recente ({stage}) no LinkedIn por {author}:\n\n{clean_body}"

                                jobs_scraped.append({
                                    "title": inferred_title,
                                    "company": author,
                                    "url": post_url,
                                    "description": desc,
                                    "location": location if location.lower() not in ("brasil", "brazil", "todo brasil", "nacional") else "Brasil",
                                    "work_mode": work_mode,
                                    "salary": "A combinar",
                                    "recipient_email": extracted_email,
                                    "source": "linkedin_post"
                                })
                        except Exception as q_err:
                            logger.debug(f"Error querying LinkedIn posts stage {stage}: {q_err}")
            except Exception as e:
                logger.error(f"Error scraping LinkedIn posts: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    async def scrape_programathor_jobs(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes jobs from Programathor (Brazilian Tech Job Board) using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_programathor_impl, keyword, location, limit, exclude_senior)

    async def _scrape_programathor_impl(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        loc_info = parse_location(location)
        kw_encoded = urllib.parse.quote(keyword.strip())

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                max_pages = min(5, max(1, (limit // 10) + 1))
                for page_num in range(1, max_pages + 1):
                    if len(jobs_scraped) >= limit:
                        break

                    url = f"https://programathor.com.br/jobs?text={kw_encoded}&page={page_num}" if page_num > 1 else f"https://programathor.com.br/jobs?text={kw_encoded}"
                    logger.info(f"Scraping Programathor (p.{page_num}): {url} (location='{loc_info['clean_loc']}', exclude_senior={exclude_senior})")
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    cards = soup.select(".cell-list")
                    if not cards:
                        break
                    
                    for card in cards:
                        if len(jobs_scraped) >= limit:
                            break
                        
                        link_el = card.select_one("a")
                        title_el = card.select_one(".cell-list-content h3")
                        
                        if not link_el or not title_el:
                            continue
                            
                        title = title_el.get_text().strip()
                        card_raw = card.get_text()
                        if is_expired_or_closed_job(title, card_text=card_raw):
                            logger.info(f"Skipping expired/closed Programathor job: '{title}'")
                            continue

                        if not is_role_relevant(title, keyword):
                            logger.info(f"Skipping non-relevant Programathor job: '{title}' (Target was '{keyword}')")
                            continue

                        if exclude_senior and is_senior_title(title):
                            logger.info(f"Skipping Senior Programathor job: '{title}'")
                            continue

                        job_url = "https://programathor.com.br" + link_el["href"]
                        
                        spans = [s.get_text(strip=True) for s in card.select(".cell-list-content-icon span")]
                        company = spans[0] if len(spans) > 0 else "Empresa Confidencial"
                        job_loc = spans[1] if len(spans) > 1 else loc_clean
                        work_mode = "Remote" if "remoto" in job_loc.lower() else "On-site"
                        desc = f"Vaga de {title} na empresa {company}. Localização: {job_loc}."

                        # Strict location validation
                        if not is_location_matching(job_loc, location, work_mode, desc):
                            continue

                        salary = "N/A"

                        jobs_scraped.append({
                            "title": title,
                            "company": company,
                            "url": job_url,
                            "description": desc,
                            "location": job_loc,
                            "work_mode": work_mode,
                            "salary": salary
                        })
            except Exception as e:
                logger.error(f"Error scraping Programathor: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    async def scrape_gupy_jobs(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes jobs from Gupy Portal search engine using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_gupy_impl, keyword, location, limit, exclude_senior)

    async def _scrape_gupy_impl(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        loc_info = parse_location(location)
        kw_encoded = urllib.parse.quote(keyword.strip())

        # Clean Gupy URL
        city_param = f"&city={urllib.parse.quote(loc_info['city'])}" if loc_info['city'] else ""
        state_param = f"&state={loc_info['state']}" if loc_info['state'] else ""
        url = f"https://portal.gupy.io/job-search/term={kw_encoded}{city_param}{state_param}"
        logger.info(f"Scraping Gupy Portal: {url} (location='{loc_info['clean_loc']}', exclude_senior={exclude_senior})")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                
                # Dynamic scrolling on Gupy
                scroll_count = min(12, max(2, (limit // 4) + 1))
                for _ in range(scroll_count):
                    await page.mouse.wheel(0, 1000)
                    await asyncio.sleep(0.8)
                
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
                    
                    if not is_role_relevant(title, keyword):
                        logger.info(f"Skipping non-relevant Gupy job: '{title}' (Target was '{keyword}')")
                        continue

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
                            
                    job_loc = "Brasil"
                    for part in text_parts:
                        if " - " in part or "Brasil" in part or "Remoto" in part or any(c in part for c in ["/", ","]):
                            job_loc = part
                            break

                    desc = f"Vaga de {title} na empresa {company}. Modalidade: {work_mode}. Localização: {job_loc}."
                    
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

                    # Strict location validation
                    if not is_location_matching(job_loc, location, work_mode, desc):
                        continue

                    jobs_scraped.append({
                        "title": title,
                        "company": company,
                        "url": job_url,
                        "description": desc,
                        "location": job_loc,
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
        loc_info = parse_location(location)
        kw_encoded = urllib.parse.quote(keyword.strip())
        l_param = f"&l={urllib.parse.quote(loc_info['clean_loc'])}" if not loc_info['is_national'] else ""

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                max_pages = min(5, max(1, (limit // 10) + 1))
                for page_idx in range(max_pages):
                    if len(jobs_scraped) >= limit:
                        break

                    start_param = f"&start={page_idx * 10}" if page_idx > 0 else ""
                    url = f"https://br.indeed.com/jobs?q={kw_encoded}{l_param}{start_param}"
                    logger.info(f"Scraping Indeed (p.{page_idx+1}): {url} (location='{loc_info['clean_loc']}', exclude_senior={exclude_senior})")
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    cards = soup.select(".job_seen_beacon, [data-jk]")
                    if not cards:
                        break
                    
                    for card in cards:
                        if len(jobs_scraped) >= limit:
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

                        if not is_role_relevant(title, keyword):
                            logger.info(f"Skipping non-relevant Indeed job: '{title}' (Target was '{keyword}')")
                            continue

                        if exclude_senior and is_senior_title(title):
                            logger.info(f"Skipping Senior Indeed job: '{title}'")
                            continue

                        job_key = card.get("data-jk", "")
                        if not job_key:
                            a_tag = card.select_one("a[href*='/rc/clk'], a[href*='/viewjob']")
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

                        # Strict location validation
                        if not is_location_matching(loc_text, location, work_mode, desc):
                            continue

                        jobs_scraped.append({
                            "title": title,
                            "company": company,
                            "url": link,
                            "description": desc,
                            "location": loc_text,
                            "work_mode": work_mode,
                            "salary": "N/A"
                        })
            except Exception as e:
                logger.error(f"Error scraping Indeed: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    # ---------------- InfoJobs Scraper ----------------
    async def scrape_infojobs_jobs(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes jobs from InfoJobs Brasil using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_infojobs_impl, keyword, location, limit, exclude_senior)

    async def _scrape_infojobs_impl(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        loc_info = parse_location(location)
        kw_encoded = urllib.parse.quote(keyword.strip())

        # Resolve exact poblacion ID from autocomplete API if location is specific
        poblacion_id = ""
        if not loc_info['is_national']:
            poblacion_id = get_infojobs_poblacion_id(loc_info['city'] or loc_info['clean_loc'])

        loc_param = f"&poblacion={poblacion_id}" if poblacion_id else (f"&campo-cidade={urllib.parse.quote(loc_info['clean_loc'])}" if not loc_info['is_national'] else "")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                max_pages = min(5, max(1, (limit // 15) + 1))
                for page_num in range(1, max_pages + 1):
                    if len(jobs_scraped) >= limit:
                        break

                    page_param = f"&page={page_num}" if page_num > 1 else ""
                    url = f"https://www.infojobs.com.br/empregos.aspx?palabra={kw_encoded}{loc_param}{page_param}"
                    logger.info(f"Scraping InfoJobs (p.{page_num}): {url} (location='{loc_info['clean_loc']}', exclude_senior={exclude_senior})")
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    cards = soup.select(".element-vaga, [data-id], .js_vacancyRow, [class*='js_vacancy']")
                    if not cards:
                        break
                    
                    for card in cards:
                        if len(jobs_scraped) >= limit:
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

                        # Strict location validation
                        if not is_location_matching(loc_text, location, work_mode, desc):
                            continue

                        jobs_scraped.append({
                            "title": title,
                            "company": company,
                            "url": link,
                            "description": desc,
                            "location": loc_text,
                            "work_mode": work_mode,
                            "salary": "N/A"
                        })
            except Exception as e:
                logger.error(f"Error scraping InfoJobs: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    # ---------------- Trabalha Brasil Scraper ----------------
    async def scrape_trabalhabrasil_jobs(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        """Scrapes jobs from Trabalha Brasil using Playwright via background proactor thread."""
        return await _run_in_proactor_thread(self._scrape_trabalhabrasil_impl, keyword, location, limit, exclude_senior)

    async def _scrape_trabalhabrasil_impl(self, keyword: str, location: str = "Brasil", limit: int = 5, exclude_senior: bool = False) -> List[Dict]:
        jobs_scraped = []
        loc_clean = location.strip() if location and location.strip() else "Brasil"
        loc_lower = loc_clean.lower()
        search_kw = keyword if loc_lower in ("brasil", "brazil", "todo brasil", "nacional") else f"{keyword} {loc_clean}"
        kw_encoded = urllib.parse.quote(search_kw)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                max_pages = min(5, max(1, (limit // 15) + 1))
                for page_num in range(1, max_pages + 1):
                    if len(jobs_scraped) >= limit:
                        break

                    url = f"https://www.trabalhabrasil.com.br/vagas-de-emprego?sp={kw_encoded}&pagina={page_num}" if page_num > 1 else f"https://www.trabalhabrasil.com.br/vagas-de-emprego?sp={kw_encoded}"
                    logger.info(f"Scraping Trabalha Brasil (p.{page_num}): {url} (exclude_senior={exclude_senior})")
                    await page.goto(url, timeout=30000)
                    await page.wait_for_timeout(2500)
                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    cards = soup.select('#jobs-wrapper .job__vacancy, .jg__job, [class*="job-card"]:not([class*="skeleton"]), article[class*="job"]')
                    if not cards:
                        cards = soup.select('a[href*="/vagas-de-emprego-em-"]')
                    if not cards:
                        break

                    for card in cards:
                        if len(jobs_scraped) >= limit:
                            break

                        title_el = card.select_one('.job__vacancy__title, h2, h3, a[title], strong, [class*="title"]')
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

                        # Strict location validation
                        if not is_location_matching(loc_text, location, work_mode, desc):
                            continue

                        jobs_scraped.append({
                            "title": title,
                            "company": company,
                            "url": link,
                            "description": desc,
                            "location": loc_text,
                            "work_mode": work_mode,
                            "salary": "N/A"
                        })
            except Exception as e:
                logger.error(f"Error scraping Trabalha Brasil: {e}")
            finally:
                await browser.close()
        return jobs_scraped

    async def ingest_new_jobs(self, db: AsyncSession, scraped_jobs: List[Dict], exclude_senior: bool = False):
        """Saves scraped jobs to the database immediately with on-demand ATS evaluation upon user interaction."""
        seen_batch_urls = set()
        seen_batch_fps = set()

        for job_dict in scraped_jobs:
            try:
                title = clean_job_title(job_dict.get("title", ""))
                company = (job_dict.get("company") or "Empresa Confidencial").strip()
                raw_url = (job_dict.get("url") or "").strip()
                canon_url = canonicalize_job_url(raw_url)

                job_dict["title"] = title
                job_dict["company"] = company
                job_dict["url"] = canon_url or raw_url

                if not title or is_unrelated_non_tech_title(title):
                    logger.info(f"Ingestion Non-Tech Gate: blocked non-tech job '{title}'")
                    continue

                if is_expired_or_closed_job(title, job_dict.get("description", "")):
                    logger.info(f"Ingestion Expiration Gate: blocked expired/closed job '{title}'")
                    continue

                if exclude_senior and is_senior_title(title):
                    logger.info(f"Ingestion Seniority Gate: blocked Senior job '{title}'")
                    continue

                # 1. In-batch Deduplication
                fp = compute_job_fingerprint(title, company)
                if canon_url and canon_url in seen_batch_urls:
                    continue
                if fp in seen_batch_fps:
                    continue

                # 2. Database URL Deduplication
                if canon_url:
                    result = await db.execute(
                        select(Job).where(
                            (Job.url == canon_url) | (Job.url == raw_url)
                        )
                    )
                    existing = result.scalars().first()
                    if existing:
                        seen_batch_urls.add(canon_url)
                        continue

                # 3. Database Title + Company Deduplication
                if company.lower() not in ("empresa confidencial", "confidencial", "anonimo", "anônimo", ""):
                    result_tc = await db.execute(
                        select(Job).where(
                            (func.lower(Job.title) == title.lower()) &
                            (func.lower(Job.company) == company.lower())
                        )
                    )
                    existing_tc = result_tc.scalars().first()
                    if existing_tc:
                        seen_batch_fps.add(fp)
                        if canon_url:
                            seen_batch_urls.add(canon_url)
                        continue

                seen_batch_urls.add(canon_url)
                seen_batch_fps.add(fp)

                # 4. Add job record immediately
                job = Job(
                    title=title,
                    company=company,
                    url=canon_url or raw_url,
                    description=job_dict["description"],
                    location=job_dict.get("location"),
                    work_mode=job_dict.get("work_mode"),
                    salary=job_dict.get("salary")
                )
                db.add(job)
                await db.flush()

                # 5. Initial placeholder application for status tracking (DISCOVERED, on-demand ATS)
                init_app = Application(
                    job_id=job.id,
                    recipient_email=job_dict.get("recipient_email"),
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

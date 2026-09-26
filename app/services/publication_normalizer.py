import re
from typing import Dict, Any, Optional, List
from app.schemas.linkedin_publication import LinkedInPublication
from app.services.search_vocabulary import normalize_role_title, normalize_term_with_aliases
from app.services.semantic_normalizer import semantic_normalizer
from app.services.scraper_service import clean_job_title, is_senior_title


class PublicationNormalizer:
    """
    Normalizes raw scraped LinkedIn job cards and post snippets into canonical LinkedInPublication models.
    Applies epistemic standards: UNKNOWN is used when evidence is absent, never fabricated.
    """

    @staticmethod
    def normalize_publication(
        raw_data: Dict[str, Any],
        query_origin: Optional[str] = None
    ) -> LinkedInPublication:
        raw_title = raw_data.get("title") or raw_data.get("headline") or ""
        cleaned_title = clean_job_title(raw_title) if raw_title else "UNKNOWN"

        raw_desc = raw_data.get("description") or raw_data.get("text") or raw_data.get("snippet") or ""
        desc = raw_desc.strip() if raw_desc else "UNKNOWN"

        company = raw_data.get("company") or raw_data.get("author") or "UNKNOWN"
        company = company.strip() if company else "UNKNOWN"

        loc = raw_data.get("location") or "UNKNOWN"
        loc = loc.strip() if loc else "UNKNOWN"

        url = raw_data.get("url") or raw_data.get("link") or "UNKNOWN"
        published_at = raw_data.get("published_at") or raw_data.get("date") or "UNKNOWN"

        # Combine text for canonical classification
        combined_text = f"{cleaned_title} {desc}"
        combined_lower = combined_text.lower()

        # 1. Classify Canonical Role
        if cleaned_title != "UNKNOWN":
            norm_role = normalize_role_title(cleaned_title)["canonical"]
        else:
            norm_role = "UNKNOWN"

        # 2. Extract Canonical Technologies using SemanticNormalizer
        norm_semantic = semantic_normalizer.normalize_text_entities(combined_text)
        technologies = list(norm_semantic.display_entities)

        # 3. Detect Work Model strictly from text evidence
        if any(w in combined_lower for w in ["remoto", "remote", "100% remoto", "home office", "teletrabalho"]):
            work_model = "Remote"
        elif any(w in combined_lower for w in ["híbrido", "hibrido", "hybrid"]):
            work_model = "Hybrid"
        elif any(w in combined_lower for w in ["presencial", "on-site", "onsite"]):
            work_model = "On-site"
        else:
            work_model = "UNKNOWN"

        # 4. Detect Employment Type strictly from text evidence
        if re.search(r"\bclt\b", combined_lower):
            employment_type = "CLT"
        elif re.search(r"\bpj\b", combined_lower):
            employment_type = "PJ"
        elif re.search(r"\best[aá]gio\b|\binternship\b", combined_lower):
            employment_type = "Estágio"
        elif re.search(r"\btrainee\b", combined_lower):
            employment_type = "Trainee"
        else:
            employment_type = "UNKNOWN"

        # 5. Detect Seniority strictly from text evidence
        if is_senior_title(cleaned_title) or any(w in combined_lower for w in ["sênior", "senior", "sr.", "tech lead", "lead engineer"]):
            seniority = "Senior"
        elif any(w in combined_lower for w in ["pleno", "pl.", "mid-level", "mid level"]):
            seniority = "Pleno"
        elif any(w in combined_lower for w in ["júnior", "junior", "jr.", "iniciante"]):
            seniority = "Junior"
        elif any(w in combined_lower for w in ["estágio", "estagio", "intern"]):
            seniority = "Estágio"
        elif any(w in combined_lower for w in ["trainee"]):
            seniority = "Trainee"
        else:
            seniority = "UNKNOWN"

        return LinkedInPublication(
            source=raw_data.get("source", "linkedin"),
            title=cleaned_title,
            description=desc,
            company=company,
            location=loc,
            work_model=work_model,
            employment_type=employment_type,
            technologies=technologies,
            role=norm_role,
            seniority=seniority,
            published_at=published_at,
            url=url,
            query_origin=query_origin,
            raw_metadata=raw_data
        )


publication_normalizer = PublicationNormalizer()

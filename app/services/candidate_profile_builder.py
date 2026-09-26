import re
from typing import List, Dict, Set, Tuple, Optional, Any
from app.schemas.candidate_search_profile import (
    CandidateSearchProfile,
    ProfileIdentity,
    ProfileTarget,
    ProfileSkills,
    ProfileExperience,
    ProfileDomains,
    ProfilePreferences,
    SearchVocabulary
)
from app.services.search_vocabulary import SearchVocabularyBuilder, normalize_role_title, normalize_term_with_aliases
from app.services.semantic_normalizer import semantic_normalizer, TECH_TAXONOMY_REGISTRY


# Domain inference patterns from company activities and descriptions
DOMAIN_KEYWORD_PATTERNS = {
    "E-commerce": [r"\be-commerce\b", r"\becommerce\b", r"\bloja\s+virtual\b", r"\bvendas\s+online\b", r"\bcheckout\b", r"\bshopify\b", r"\bwoo\b"],
    "Fintech": [r"\bfintech\b", r"\bpagamentos\b", r"\bbanco\b", r"\bfinanceir[oa]\b", r"\bpix\b", r"\bcr[eé]dito\b"],
    "SaaS": [r"\bsaas\b", r"\bsoftware\s+as\s+a\s+service\b", r"\bassinatura\b", r"\bmultitenan\b"],
    "Marketing / Web": [r"\blanding\s+pages?\b", r"\bmarketing\b", r"\bag[eê]ncia\b", r"\bconvers[aã]o\b", r"\bseo\b"],
    "Educação / EdTech": [r"\bedtech\b", r"\beduca[cç][aã]o\b", r"\be-learning\b", r"\bcursos\b"]
}


class CandidateProfileBuilder:
    """
    Builds the versioned 1.0 CandidateSearchProfile JSON contract.
    Strictly preserves epistemic rigor: facts (observed_roles) != aspirations (target_roles).
    When information is not determined, sets 'UNKNOWN' instead of converting to 'ABSENT' or fabricating identities.
    """

    @staticmethod
    def build_from_user_profile(
        user_profile: Any,
        resumes: Optional[List[Any]] = None,
        explicit_target_roles: Optional[List[str]] = None,
        explicit_seniority: Optional[str] = None
    ) -> CandidateSearchProfile:
        # 1. Identity & Observed Roles
        observed_roles: List[str] = []
        experiences_list: List[ProfileExperience] = []
        observed_domains: Set[str] = set()

        raw_exps = getattr(user_profile, "experiences", []) or []
        for exp in raw_exps:
            r_title = getattr(exp, "role", None) or "UNKNOWN"
            if r_title != "UNKNOWN":
                norm_r = normalize_role_title(r_title)["canonical"]
                if norm_r not in observed_roles:
                    observed_roles.append(norm_r)

            company_name = getattr(exp, "company", None) or "UNKNOWN"
            desc = getattr(exp, "description", None) or ""
            skills_used = getattr(exp, "skills_used", []) or []

            # Extract activities and domains from description
            exp_domains: List[str] = []
            for d_name, patterns in DOMAIN_KEYWORD_PATTERNS.items():
                if any(re.search(pat, desc.lower()) for pat in patterns):
                    exp_domains.append(d_name)
                    observed_domains.add(d_name)

            activities = [p.strip() for p in re.split(r'[\n;•\.-]+', desc) if len(p.strip()) > 10]

            experiences_list.append(ProfileExperience(
                role=r_title,
                company=company_name,
                domain=exp_domains,
                technologies=skills_used,
                activities=activities[:4],
                duration_months=getattr(exp, "duration_months", None)
            ))

        # Determine area from observed technologies
        all_techs_raw = list(getattr(user_profile, "technologies", []) or [])
        for exp in raw_exps:
            all_techs_raw.extend(getattr(exp, "skills_used", []) or [])

        # 2. Categorize Skills into specific semantic buckets
        languages: List[str] = []
        frameworks: List[str] = []
        frontend: List[str] = []
        backend: List[str] = []
        databases: List[str] = [d for d in (getattr(user_profile, "databases", []) or []) if d]
        cloud: List[str] = [c for c in (getattr(user_profile, "cloud_providers", []) or []) if c]
        tools: List[str] = [t for t in (getattr(user_profile, "devops_tools", []) or []) if t]

        for t in all_techs_raw:
            if not t:
                continue
            norm_t = normalize_term_with_aliases(t)["canonical"]
            t_lower = norm_t.lower()

            # Classify using TECH_TAXONOMY_REGISTRY
            matched_entry = next(
                (item for item in TECH_TAXONOMY_REGISTRY if t_lower == item.canonical_id or t_lower == item.display_name.lower() or t_lower in [a.lower() for a in item.aliases]),
                None
            )
            if matched_entry:
                disp = matched_entry.display_name
                # Simplify display name for clean profile presentation (e.g. 'TypeScript (TS)' -> 'TypeScript')
                clean_name = disp.split(" (")[0].split(" /")[0]
                if matched_entry.category == "language" and clean_name not in languages:
                    languages.append(clean_name)
                elif matched_entry.category == "framework":
                    if clean_name not in frameworks:
                        frameworks.append(clean_name)
                    if any(f in t_lower for f in ["react", "vue", "angular", "html", "css", "tailwind", "twig"]) and clean_name not in frontend:
                        frontend.append(clean_name)
                    if any(b in t_lower for b in ["node", "nest", "laravel", "express", "fastapi", "django", "spring"]) and clean_name not in backend:
                        backend.append(clean_name)
                elif matched_entry.category == "database" and clean_name not in databases:
                    databases.append(clean_name)
                elif matched_entry.category == "devops" and clean_name not in tools:
                    tools.append(clean_name)
            else:
                if norm_t not in frameworks:
                    frameworks.append(norm_t)

        # 3. Target Roles and Seniority
        target_roles_raw = explicit_target_roles or getattr(user_profile, "desired_roles", []) or []
        target_roles = []
        for tr in target_roles_raw:
            if tr and tr != "UNKNOWN":
                norm_tr = normalize_role_title(tr)["canonical"]
                if norm_tr not in target_roles:
                    target_roles.append(norm_tr)

        if not target_roles and observed_roles:
            target_roles = list(observed_roles)

        target_sen_raw = explicit_seniority or getattr(user_profile, "seniority_level", None) or "Junior"
        target_sen = [target_sen_raw] if isinstance(target_sen_raw, str) else list(target_sen_raw)

        # 4. Identity Area
        if frontend and backend:
            professional_area = "Desenvolvimento de Software Full Stack"
        elif backend:
            professional_area = "Desenvolvimento de Software Backend"
        elif frontend:
            professional_area = "Desenvolvimento de Software Frontend"
        else:
            professional_area = "Desenvolvimento de Software"

        # 5. Domains
        domains_experienced = list(observed_domains) if observed_domains else ["Web / Software Development"]
        domains_adjacent = ["SaaS", "E-commerce", "Fintech"]

        # 6. Preferences
        raw_work_mode = getattr(user_profile, "work_mode", None) or "Remote"
        work_models = [raw_work_mode] if isinstance(raw_work_mode, str) else list(raw_work_mode)
        raw_loc = getattr(user_profile, "location", None) or "Brasil"
        locations = [raw_loc] if isinstance(raw_loc, str) else list(raw_loc)

        # 7. Search Vocabulary
        all_candidate_roles = list(dict.fromkeys(target_roles + observed_roles))
        search_vocab = SearchVocabularyBuilder.build(
            roles=all_candidate_roles,
            technologies=languages + frameworks + databases,
            domains=domains_experienced,
            seniority=target_sen[0] if target_sen else "Junior"
        )

        return CandidateSearchProfile(
            profile_version="1.0",
            identity=ProfileIdentity(
                professional_area=professional_area,
                roles_observed=observed_roles
            ),
            target=ProfileTarget(
                roles=target_roles,
                seniority=target_sen
            ),
            skills=ProfileSkills(
                languages=languages,
                frameworks=frameworks,
                frontend=frontend,
                backend=backend,
                databases=databases,
                cloud=cloud,
                tools=tools
            ),
            experience=experiences_list,
            domains=ProfileDomains(
                experienced=domains_experienced,
                adjacent=domains_adjacent
            ),
            preferences=ProfilePreferences(
                work_model=work_models,
                locations=locations,
                employment_types=["CLT", "PJ", "Remoto"]
            ),
            search_vocabulary=search_vocab
        )

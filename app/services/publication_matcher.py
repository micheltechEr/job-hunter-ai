import logging
from typing import List, Dict, Tuple, Optional, Any
from app.schemas.candidate_search_profile import CandidateSearchProfile
from app.schemas.linkedin_publication import LinkedInPublication, MatchBreakdown, PublicationMatchResult
from app.services.embedding_service import embedding_service
from app.services.semantic_normalizer import semantic_normalizer, TECH_TAXONOMY_REGISTRY

logger = logging.getLogger("job_hunter.publication_matcher")


class QueryPerformanceTracker:
    """Tracks search query efficiency and relevance yield for future optimization."""
    def __init__(self):
        self._stats: Dict[str, Dict[str, int]] = {}

    def record(self, query: str, results_found: int, relevant_results: int):
        if not query:
            return
        if query not in self._stats:
            self._stats[query] = {"results_found": 0, "relevant_results": 0}
        self._stats[query]["results_found"] += results_found
        self._stats[query]["relevant_results"] += relevant_results

    def get_stats(self) -> Dict[str, Dict[str, Any]]:
        report = {}
        for q, data in self._stats.items():
            rf = data["results_found"]
            rr = data["relevant_results"]
            rate = round((rr / rf * 100), 1) if rf > 0 else 0.0
            report[q] = {
                "results_found": rf,
                "relevant_results": rr,
                "relevance_rate_pct": rate
            }
        return report


class PublicationMatcher:
    """
    Hybrid matching engine combining deterministic rule-based evaluation with semantic embeddings.
    Provides complete explainability and auditability for every publication match.
    """

    def __init__(self):
        self.performance_tracker = QueryPerformanceTracker()

    async def evaluate_publication(
        self,
        candidate_profile: CandidateSearchProfile,
        publication: LinkedInPublication
    ) -> PublicationMatchResult:
        # 1. Rule Match: Roles
        cand_roles_lower = [r.lower() for r in candidate_profile.get_search_roles()]
        pub_role_lower = publication.role.lower() if publication.role != "UNKNOWN" else ""
        pub_title_lower = publication.title.lower() if publication.title != "UNKNOWN" else ""

        matched_roles: List[str] = []
        if pub_role_lower and any(r in pub_role_lower or pub_role_lower in r for r in cand_roles_lower):
            role_match = "strong"
            matched_roles.append(publication.role)
        elif pub_title_lower and any(r in pub_title_lower for r in cand_roles_lower):
            role_match = "strong"
            matched_roles.append(publication.title)
        elif any(w in pub_title_lower for w in ["desenvolvedor", "developer", "dev", "programador", "software"]):
            role_match = "moderate"
            matched_roles.append("Desenvolvedor (Genérico)")
        else:
            role_match = "none"

        # 2. Rule Match: Skills & Technologies (Semantic taxonomy comparison)
        cand_all_techs = (
            candidate_profile.skills.languages +
            candidate_profile.skills.frameworks +
            candidate_profile.skills.databases +
            candidate_profile.skills.tools
        )
        cand_techs_norm = [t.lower().strip() for t in cand_all_techs]

        # Audit canonical compatibility via SemanticNormalizer
        pub_full_text = f"{publication.title} {publication.description}"
        cand_full_text = f"{' '.join(cand_all_techs)} {' '.join([f'{e.role} {e.company}' for e in candidate_profile.experience])}"
        
        cand_sem = semantic_normalizer.normalize_text_entities(cand_full_text)
        pub_sem = semantic_normalizer.normalize_text_entities(pub_full_text)
        
        is_stack_compat, matched_canonical, missing_mandatory = semantic_normalizer.audit_stack_compatibility(cand_sem, pub_sem)

        matched_skills: List[str] = list(matched_canonical)
        unmatched_requirements: List[str] = list(missing_mandatory)

        if not is_stack_compat or missing_mandatory:
            skill_match = "none"
        elif len(matched_skills) >= 3:
            skill_match = "strong"
        elif len(matched_skills) >= 1:
            skill_match = "moderate"
        elif any(t in pub_full_text.lower() for t in cand_techs_norm):
            skill_match = "weak"
        else:
            skill_match = "none"

        # 3. Rule Match: Domains
        matched_domains: List[str] = []
        cand_domains_lower = [d.lower() for d in candidate_profile.domains.experienced]
        for d in candidate_profile.domains.experienced:
            if d.lower() in pub_full_text.lower():
                matched_domains.append(d)

        if matched_domains:
            domain_match = "strong"
        elif any(adj.lower() in pub_full_text.lower() for adj in candidate_profile.domains.adjacent):
            domain_match = "moderate"
            matched_domains.append("Adjacent Domain")
        else:
            domain_match = "none"

        # 4. Rule Match: Seniority
        cand_seniority = candidate_profile.target.seniority[0].lower() if candidate_profile.target.seniority else "junior"
        pub_seniority = publication.seniority.lower()

        if pub_seniority == "unknown":
            seniority_match = "moderate" # Open level
        elif pub_seniority == cand_seniority:
            seniority_match = "strong"
        elif cand_seniority in ["junior", "estagio", "trainee"] and pub_seniority == "senior":
            seniority_match = "none"
        elif cand_seniority in ["junior", "trainee"] and pub_seniority in ["junior", "trainee", "estágio"]:
            seniority_match = "strong"
        elif cand_seniority in ["pleno"] and pub_seniority in ["junior", "pleno"]:
            seniority_match = "strong"
        else:
            seniority_match = "weak"

        # 5. Rule Match: Location & Work Model
        matched_preferences: List[str] = []
        if publication.work_model in ["Remote", "100% Remoto", "Home Office"]:
            work_model_match = "strong"
            matched_preferences.append("Remote")
        elif publication.work_model == "Hybrid":
            work_model_match = "moderate"
            matched_preferences.append("Hybrid")
        elif publication.work_model == "UNKNOWN":
            work_model_match = "moderate"
        else:
            work_model_match = "weak"

        if publication.location in ["Brasil", "Remote", "Remoto", "UNKNOWN"] or any(loc.lower() in publication.location.lower() for loc in candidate_profile.preferences.locations):
            location_match = "strong"
            matched_preferences.append(publication.location)
        else:
            location_match = "moderate"

        # 6. Semantic Embedding Similarity
        cand_embed_text = f"{candidate_profile.identity.professional_area}. Cargos: {', '.join(candidate_profile.get_search_roles())}. Tecnologias: {', '.join(cand_all_techs)}. Domínios: {', '.join(candidate_profile.domains.experienced)}"
        pub_embed_text = f"{publication.title}. Empresa: {publication.company}. Local: {publication.location}. Requisitos: {publication.description[:500]}"

        cand_vec = await embedding_service.get_embedding(cand_embed_text)
        pub_vec = await embedding_service.get_embedding(pub_embed_text)
        semantic_sim = embedding_service.calculate_similarity(cand_vec, pub_vec)
        semantic_sim = max(0.0, min(1.0, round(float(semantic_sim), 3)))

        # 7. Calculate Composite Score
        score = 0.0

        # Role contribution (max 25)
        role_scores = {"strong": 25.0, "moderate": 15.0, "weak": 5.0, "none": 0.0}
        score += role_scores.get(role_match, 0.0)

        # Skill contribution (max 35)
        skill_scores = {"strong": 35.0, "moderate": 22.0, "weak": 10.0, "none": 0.0}
        score += skill_scores.get(skill_match, 0.0)

        # Seniority contribution (max 15)
        sen_scores = {"strong": 15.0, "moderate": 10.0, "weak": 3.0, "none": 0.0}
        score += sen_scores.get(seniority_match, 0.0)

        # Work Model / Location (max 10)
        wm_scores = {"strong": 10.0, "moderate": 6.0, "weak": 0.0, "none": 0.0}
        score += wm_scores.get(work_model_match, 0.0)

        # Semantic Similarity contribution (max 15)
        score += semantic_sim * 15.0

        # Hard Stack Gating: If missing mandatory core language, cap score to <= 30
        if not is_stack_compat or missing_mandatory:
            score = min(score, 25.0)

        # Hard Seniority Gating: If junior candidate vs Senior job, cap score to <= 30
        if cand_seniority in ["junior", "estagio", "trainee"] and pub_seniority == "senior":
            score = min(score, 25.0)

        final_score = int(round(score))
        final_score = max(0, min(100, final_score))

        # Determine Relevance Tier
        if final_score >= 75:
            relevance_tier = "HIGH_RELEVANCE"
            is_relevant = True
        elif final_score >= 55:
            relevance_tier = "MODERATE_RELEVANCE"
            is_relevant = True
        elif final_score >= 40:
            relevance_tier = "LOW_RELEVANCE"
            is_relevant = False
        else:
            relevance_tier = "NOT_RELEVANT"
            is_relevant = False

        # Build Explainability Rationale
        if missing_mandatory:
            explanation = f"Incompatibilidade crítica: Vaga exige {', '.join(missing_mandatory)}, ausente no perfil do candidato."
        elif is_relevant:
            explanation = f"Relevância {relevance_tier.replace('_', ' ')} ({final_score}/100). " \
                          f"Cargo: {role_match} match | Skills: {len(matched_skills)} tecnologias correspondentes ({', '.join(matched_skills) if matched_skills else 'base técnica compatível'}) | " \
                          f"Similaridade semântica: {semantic_sim:.2f}."
        else:
            explanation = f"Baixa correspondência ({final_score}/100). Requisitos técnicos ou senioridade não atendem aos critérios de busca."

        breakdown = MatchBreakdown(
            semantic_similarity=semantic_sim,
            role_match=role_match,
            skill_match=skill_match,
            domain_match=domain_match,
            seniority_match=seniority_match,
            location_match=location_match,
            work_model_match=work_model_match,
            composite_score=final_score,
            is_relevant=is_relevant,
            relevance_tier=relevance_tier
        )

        return PublicationMatchResult(
            publication=publication,
            breakdown=breakdown,
            matched_roles=matched_roles,
            matched_skills=matched_skills,
            matched_domains=matched_domains,
            matched_preferences=matched_preferences,
            unmatched_requirements=unmatched_requirements,
            explanation=explanation
        )


publication_matcher = PublicationMatcher()

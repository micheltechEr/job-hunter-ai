import re
from typing import List, Dict, Set, Optional, Any
from pydantic import BaseModel, Field
from app.schemas.candidate_search_profile import CandidateSearchProfile


class GeneratedQuery(BaseModel):
    query: str = Field(description="Exact search query string ready for search engine/LinkedIn")
    type: str = Field(description="Strategy name (e.g. role_skill_combination, synonym_expansion)")
    sources: Dict[str, Any] = Field(default={}, description="Audit trail of which profile components originated this query")
    priority: str = Field(default="medium", description="high, medium, low")
    layer: int = Field(default=1, description="Progressive generation layer (1=high precision, 2=expansion, 3=discovery, 4=domain)")
    expected_intent: str = Field(default="job_posting", description="Intended search intent")


def normalize_query_text(query: str) -> str:
    """Collapses spaces and standardizes quotes for accurate deduplication."""
    if not query:
        return ""
    q = re.sub(r'\s+', ' ', query).strip()
    return q


class QueryGenerator:
    """
    Isolated Query Generation Engine.
    Transforms a structured CandidateSearchProfile into prioritized, deduplicated,
    and metadata-backed search queries across 4 progressive layers and 6 strategies.
    """

    def __init__(self, max_queries: int = 16):
        self.max_queries = max_queries

    def generate_queries(
        self,
        profile: CandidateSearchProfile,
        max_queries: Optional[int] = None
    ) -> List[GeneratedQuery]:
        limit = max_queries or self.max_queries
        generated: List[GeneratedQuery] = []
        seen_normalized: Set[str] = set()

        def _add_query(q_str: str, q_type: str, sources: Dict[str, Any], priority: str, layer: int):
            norm = normalize_query_text(q_str).lower()
            if not norm or norm in seen_normalized:
                return
            seen_normalized.add(norm)
            generated.append(GeneratedQuery(
                query=normalize_query_text(q_str),
                type=q_type,
                sources=sources,
                priority=priority,
                layer=layer,
                expected_intent="job_posting"
            ))

        roles = profile.target.roles or profile.identity.roles_observed or ["Desenvolvedor"]
        primary_role = roles[0] if roles else "Desenvolvedor"
        
        # Primary technologies: languages first, then core frameworks
        primary_techs = profile.skills.languages[:3] + profile.skills.frameworks[:3]
        if not primary_techs:
            primary_techs = profile.search_vocabulary.technologies[:4] or ["React", "Node.js"]

        domains = profile.domains.experienced or profile.search_vocabulary.domains or ["E-commerce"]
        vocab_synonyms = profile.search_vocabulary.synonyms or {}

        # =========================================================================
        # LAYER 1: ALTA PRECISÃO (Combinações diretamente sustentadas pelo perfil)
        # =========================================================================
        # Estratégia 1: Role + Single Skill
        for r in roles[:2]:
            for t in primary_techs[:3]:
                _add_query(
                    q_str=f'"{r}" {t}',
                    q_type="role_skill_combination",
                    sources={"role": r, "skills": [t]},
                    priority="high",
                    layer=1
                )

        # Estratégia 2: Role + Multiple Skills (Múltiplas skills core)
        if len(primary_techs) >= 2:
            tech_combo = f"{primary_techs[0]} {primary_techs[1]}"
            _add_query(
                q_str=f'"{primary_role}" {tech_combo}',
                q_type="role_multiskill_combination",
                sources={"role": primary_role, "skills": [primary_techs[0], primary_techs[1]]},
                priority="high",
                layer=1
            )

        # =========================================================================
        # LAYER 2: EXPANSÃO (Sinônimos técnicos e variações de papéis)
        # =========================================================================
        # Estratégia 5: Synonyms / Variações de termos de cargo
        for r in profile.search_vocabulary.roles:
            if r.lower() != primary_role.lower():
                for t in primary_techs[:2]:
                    _add_query(
                        q_str=f'"{r}" {t}',
                        q_type="synonym_expansion",
                        sources={"role": r, "skills": [t], "synonym_of": primary_role},
                        priority="medium",
                        layer=2
                    )

        # Estratégia 6: Role Alternativa (ex: Backend quando target é Full Stack)
        alt_roles = [r for r in profile.identity.roles_observed if r.lower() != primary_role.lower()]
        for alt_r in alt_roles[:2]:
            for t in primary_techs[:2]:
                _add_query(
                    q_str=f'"{alt_r}" {t}',
                    q_type="alternative_role",
                    sources={"role": alt_r, "skills": [t]},
                    priority="medium",
                    layer=2
                )

        # =========================================================================
        # LAYER 3: DESCOBERTA (Consultas técnicas amplas)
        # =========================================================================
        for t in primary_techs[:3]:
            _add_query(
                q_str=f'{t} developer',
                q_type="technology_discovery",
                sources={"skills": [t], "broad_keyword": "developer"},
                priority="medium",
                layer=3
            )

        # =========================================================================
        # LAYER 4: DOMÍNIO (Consultas contextuais de domínio e negócio)
        # =========================================================================
        # Estratégia 3: Role + Domain
        for dom in domains[:2]:
            _add_query(
                q_str=f'"{primary_role}" {dom.lower()}',
                q_type="role_domain_combination",
                sources={"role": primary_role, "domain": dom},
                priority="low",
                layer=4
            )

        # Estratégia 4: Technology + Domain
        for t in primary_techs[:2]:
            for dom in domains[:2]:
                _add_query(
                    q_str=f'{t} {dom.lower()}',
                    q_type="technology_domain_combination",
                    sources={"skills": [t], "domain": dom},
                    priority="low",
                    layer=4
                )

        # Priority Sorting: High -> Medium -> Low (preserving stability)
        priority_weights = {"high": 0, "medium": 1, "low": 2}
        generated.sort(key=lambda q: (priority_weights.get(q.priority, 1), q.layer))

        return generated[:limit]

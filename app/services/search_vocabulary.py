import re
from typing import Dict, List, Set, Optional, Tuple, Any
from pydantic import BaseModel
from app.schemas.candidate_search_profile import SearchVocabulary


ROLE_SYNONYMS_MAP: Dict[str, List[str]] = {
    "desenvolvedor": ["developer", "dev", "programador", "engenheiro de software"],
    "developer": ["desenvolvedor", "dev", "software engineer"],
    "backend": ["back-end", "back end"],
    "back-end": ["backend", "back end"],
    "frontend": ["front-end", "front end"],
    "front-end": ["frontend", "front end"],
    "fullstack": ["full stack", "full-stack"],
    "full stack": ["fullstack", "full-stack"],
    "estágio": ["estagio", "intern", "internship"],
    "trainee": ["trainee", "programa de formação"],
    "junior": ["jr", "júnior"],
    "pleno": ["pl", "mid", "mid-level"]
}

TECH_CANONICAL_ALIASES: Dict[str, Dict[str, Any]] = {
    "node.js": {"canonical": "Node.js", "aliases": ["Node", "NodeJS", "Node.JS", "Node js"]},
    "react": {"canonical": "React", "aliases": ["React.js", "ReactJS", "React JS"]},
    "vue": {"canonical": "Vue", "aliases": ["Vue.js", "VueJS", "Vue 3"]},
    "typescript": {"canonical": "TypeScript", "aliases": ["TS"]},
    "javascript": {"canonical": "JavaScript", "aliases": ["JS", "ES6+"]},
    "php": {"canonical": "PHP", "aliases": ["PHP 8", "PHP 7"]},
    "laravel": {"canonical": "Laravel", "aliases": ["Laravel Framework"]},
    "nestjs": {"canonical": "Nest.js", "aliases": ["NestJS", "Nest"]},
    "nextjs": {"canonical": "Next.js", "aliases": ["NextJS", "Next"]},
    "postgresql": {"canonical": "PostgreSQL", "aliases": ["Postgres", "PGSQL"]},
    "mysql": {"canonical": "MySQL", "aliases": ["My SQL"]},
    "redis": {"canonical": "Redis", "aliases": ["Redis Cache"]},
    "supabase": {"canonical": "Supabase", "aliases": ["Supabase DB"]},
    "docker": {"canonical": "Docker", "aliases": ["Containers"]},
    "aws": {"canonical": "AWS", "aliases": ["Amazon Web Services"]},
    "git": {"canonical": "Git", "aliases": ["GitHub", "GitLab"]}
}


def normalize_term_with_aliases(term: str) -> Dict[str, Any]:
    """
    Normalizes a term recognizing equivalent variants while preserving canonical and aliases.
    Example: 'NodeJS' -> {'canonical': 'Node.js', 'aliases': ['Node', 'NodeJS', 'Node.JS', 'Node js']}
    """
    if not term:
        return {"canonical": "UNKNOWN", "aliases": []}

    cleaned = term.strip().lower()
    for key, data in TECH_CANONICAL_ALIASES.items():
        if cleaned == key or cleaned in [a.lower() for a in data["aliases"]]:
            return {
                "canonical": data["canonical"],
                "aliases": [a for a in data["aliases"] if a.lower() != data["canonical"].lower()]
            }

    # Default fallback preserves original casing
    return {"canonical": term.strip(), "aliases": []}


def normalize_role_title(role_title: str) -> Dict[str, Any]:
    """
    Normalizes role variations (e.g. 'Full Stack Developer', 'Desenvolvedor Full Stack', 'Fullstack Dev').
    Preserves canonical form and derived search aliases.
    """
    if not role_title:
        return {"canonical": "UNKNOWN", "aliases": []}

    t = role_title.strip()
    t_lower = t.lower()

    if any(p in t_lower for p in ["full stack", "fullstack", "full-stack"]):
        if any(w in t_lower for w in ["developer", "dev", "desenvolvedor", "programador", "engineer"]):
            return {
                "canonical": "Desenvolvedor Full Stack",
                "aliases": ["Full Stack Developer", "Fullstack Developer", "Desenvolvedor Fullstack", "Full Stack Dev"]
            }

    if any(p in t_lower for p in ["backend", "back-end", "back end"]):
        if any(w in t_lower for w in ["developer", "dev", "desenvolvedor", "programador", "engineer"]):
            return {
                "canonical": "Desenvolvedor Backend",
                "aliases": ["Backend Developer", "Back-End Developer", "Desenvolvedor Back-End", "Backend Dev"]
            }

    if any(p in t_lower for p in ["frontend", "front-end", "front end"]):
        if any(w in t_lower for w in ["developer", "dev", "desenvolvedor", "programador", "engineer"]):
            return {
                "canonical": "Desenvolvedor Frontend",
                "aliases": ["Frontend Developer", "Front-End Developer", "Desenvolvedor Front-End", "Frontend Dev"]
            }

    return {"canonical": t, "aliases": []}


class SearchVocabularyBuilder:
    """Derives search vocabulary with technical synonyms, canonical terms, and evidence-based exclusions."""

    @staticmethod
    def build(
        roles: List[str],
        technologies: List[str],
        domains: List[str],
        seniority: str = "Junior",
        explicit_exclude: Optional[List[str]] = None
    ) -> SearchVocabulary:
        vocab_roles: List[str] = []
        synonyms: Dict[str, List[str]] = {}

        # 1. Process Roles and build canonical forms & aliases
        for r in roles:
            if not r or r == "UNKNOWN":
                continue
            norm = normalize_role_title(r)
            if norm["canonical"] not in vocab_roles:
                vocab_roles.append(norm["canonical"])
            for alias in norm["aliases"]:
                if alias not in vocab_roles:
                    vocab_roles.append(alias)

        # 2. Derive Role Synonyms
        for word, syn_list in ROLE_SYNONYMS_MAP.items():
            if any(word in r.lower() for r in vocab_roles):
                synonyms[word] = list(syn_list)

        # 3. Process Technologies and build canonical forms
        vocab_techs: List[str] = []
        for t in technologies:
            if not t or t == "UNKNOWN":
                continue
            t_norm = normalize_term_with_aliases(t)
            canon = t_norm["canonical"]
            if canon not in vocab_techs:
                vocab_techs.append(canon)
            if t_norm["aliases"]:
                synonyms[canon.lower()] = t_norm["aliases"]

        # 4. Process Domains
        vocab_domains: List[str] = [d for d in domains if d and d != "UNKNOWN"]

        # 5. Build Exclusions strictly based on evidence
        exclusions: List[str] = []
        if explicit_exclude:
            for ex in explicit_exclude:
                if ex and ex not in exclusions:
                    exclusions.append(ex)
        else:
            # Evidence-based derivation: If candidate seniority is Junior/Trainee/Estágio
            sen_clean = (seniority or "").lower().strip()
            if sen_clean in ["junior", "jr", "trainee", "estagio", "estágio", "estagiario"]:
                exclusions = ["Senior", "Sr", "Tech Lead", "Staff", "Principal", "Manager", "Head", "Diretor", "Gerente"]

        return SearchVocabulary(
            roles=vocab_roles,
            technologies=vocab_techs,
            domains=vocab_domains,
            synonyms=synonyms,
            exclude=exclusions
        )

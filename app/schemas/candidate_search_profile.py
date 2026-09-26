import json
from typing import Dict, List, Optional, Any, Union
from pydantic import BaseModel, Field


class ProfileIdentity(BaseModel):
    """
    Candidate observed facts: real background extracted from resume history.
    Strictly differentiated from target aspirations.
    """
    professional_area: str = Field(default="UNKNOWN", description="Main technical area (e.g. Software Development, Data Engineering)")
    roles_observed: List[str] = Field(default=[], description="Concrete titles held in past experiences")


class ProfileTarget(BaseModel):
    """Candidate targeted goals for upcoming career transitions."""
    roles: List[str] = Field(default=[], description="Explicit target roles desired for new opportunities")
    seniority: List[str] = Field(default=[], description="Desired or realistic seniority levels (e.g. Junior, Pleno)")


class ProfileSkills(BaseModel):
    """Categorized technical capabilities."""
    languages: List[str] = Field(default=[], description="Core programming languages")
    frameworks: List[str] = Field(default=[], description="Web and backend frameworks")
    frontend: List[str] = Field(default=[], description="Frontend libraries and UI frameworks")
    backend: List[str] = Field(default=[], description="Backend technologies and runtimes")
    databases: List[str] = Field(default=[], description="Databases and storage engines")
    cloud: List[str] = Field(default=[], description="Cloud providers and infra services")
    tools: List[str] = Field(default=[], description="DevOps, CI/CD and tooling")


class ProfileExperience(BaseModel):
    """Structured past professional experience with preserved context."""
    role: str = Field(default="UNKNOWN", description="Role title in the position")
    company: Optional[str] = Field(default="UNKNOWN", description="Company or Organization")
    domain: List[str] = Field(default=[], description="Industry vertical or domain (e.g. E-commerce, Fintech, SaaS)")
    technologies: List[str] = Field(default=[], description="Specific technologies applied in this role")
    activities: List[str] = Field(default=[], description="Main engineering activities and responsibilities")
    duration_months: Optional[int] = Field(default=None, description="Calculated duration in months if determinable")


class ProfileDomains(BaseModel):
    """Industry and business domains."""
    experienced: List[str] = Field(default=[], description="Domains where candidate has verifiable work experience")
    adjacent: List[str] = Field(default=[], description="Adjacent domains where candidate skills directly transfer")


class ProfilePreferences(BaseModel):
    """Explicit employment and logistical preferences."""
    work_model: List[str] = Field(default=[], description="Remote, Hybrid, On-site")
    locations: List[str] = Field(default=[], description="Target cities, states, or countries")
    employment_types: List[str] = Field(default=[], description="CLT, PJ, Contract, Internship")


class SearchVocabulary(BaseModel):
    """Derived vocabulary specifically crafted for targeted search query generation."""
    roles: List[str] = Field(default=[], description="Normalized canonical roles and top search aliases")
    technologies: List[str] = Field(default=[], description="Primary discriminant technologies")
    domains: List[str] = Field(default=[], description="High-relevance domain tags")
    synonyms: Dict[str, List[str]] = Field(default={}, description="Synonym dictionary for technical and role terms")
    exclude: List[str] = Field(default=[], description="Exclusion keywords supported by explicit evidence or settings")


class CandidateSearchProfile(BaseModel):
    """
    Versioned canonical intermediate contract between Resume and Search Query Engines.
    Strictly separates facts from inferences and avoids raw string flattening.
    """
    profile_version: str = Field(default="1.0", description="Schema version")
    identity: ProfileIdentity = Field(default_factory=ProfileIdentity)
    target: ProfileTarget = Field(default_factory=ProfileTarget)
    skills: ProfileSkills = Field(default_factory=ProfileSkills)
    experience: List[ProfileExperience] = Field(default_factory=list)
    domains: ProfileDomains = Field(default_factory=ProfileDomains)
    preferences: ProfilePreferences = Field(default_factory=ProfilePreferences)
    search_vocabulary: SearchVocabulary = Field(default_factory=SearchVocabulary)

    def to_canonical_json(self, indent: int = 2) -> str:
        """Serializes the profile into an explicit canonical JSON string contract."""
        return json.dumps(self.model_dump(), indent=indent, ensure_ascii=False)

    def get_primary_languages(self) -> List[str]:
        """Returns verified primary programming languages."""
        return list(self.skills.languages)

    def get_search_roles(self) -> List[str]:
        """Returns prioritize search roles: target roles first, then observed roles."""
        roles = []
        for r in self.target.roles:
            if r and r != "UNKNOWN" and r not in roles:
                roles.append(r)
        for r in self.identity.roles_observed:
            if r and r != "UNKNOWN" and r not in roles:
                roles.append(r)
        return roles

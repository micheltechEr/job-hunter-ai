from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field


class LinkedInPublication(BaseModel):
    """
    Normalized intermediate representation of a LinkedIn job publication or hiring post.
    Strictly follows epistemic rigor: missing fields default to 'UNKNOWN' rather than fabricating details.
    """
    source: str = Field(default="linkedin", description="Data source identifier (e.g. linkedin, linkedin_post)")
    title: str = Field(default="UNKNOWN", description="Publication title or headline")
    description: str = Field(default="UNKNOWN", description="Full or extracted publication text")
    company: str = Field(default="UNKNOWN", description="Company or author organization name")
    location: str = Field(default="UNKNOWN", description="City, State, Country or region")
    work_model: str = Field(default="UNKNOWN", description="Remote, Hybrid, On-site, or UNKNOWN")
    employment_type: str = Field(default="UNKNOWN", description="CLT, PJ, Estágio, or UNKNOWN")
    technologies: List[str] = Field(default=[], description="Extracted canonical technologies")
    role: str = Field(default="UNKNOWN", description="Normalized role classification")
    seniority: str = Field(default="UNKNOWN", description="Junior, Pleno, Senior, Trainee, or UNKNOWN")
    published_at: Optional[str] = Field(default="UNKNOWN", description="Publication timestamp or relative age")
    url: str = Field(default="UNKNOWN", description="Direct URL to publication")
    query_origin: Optional[str] = Field(default=None, description="The specific search query that retrieved this item")
    raw_metadata: Dict[str, Any] = Field(default={}, description="Raw scraper payload for auditability")


class MatchBreakdown(BaseModel):
    """Explanatory match metrics separating rule checks from semantic embeddings."""
    semantic_similarity: float = Field(default=0.0, description="Cosine similarity score (0.0 to 1.0)")
    role_match: str = Field(default="none", description="strong, moderate, weak, none")
    skill_match: str = Field(default="none", description="strong, moderate, weak, none")
    domain_match: str = Field(default="none", description="strong, moderate, weak, none")
    seniority_match: str = Field(default="none", description="strong, moderate, weak, none")
    location_match: str = Field(default="none", description="strong, moderate, weak, none")
    work_model_match: str = Field(default="none", description="strong, moderate, weak, none")
    composite_score: int = Field(default=0, description="Final weighted composite score (0 to 100)")
    is_relevant: bool = Field(default=False, description="True if publication passes relevance threshold")
    relevance_tier: str = Field(default="NOT_RELEVANT", description="HIGH_RELEVANCE, MODERATE_RELEVANCE, LOW_RELEVANCE, NOT_RELEVANT")


class PublicationMatchResult(BaseModel):
    """Complete evaluation report with full traceability and human-readable explainability."""
    publication: LinkedInPublication
    breakdown: MatchBreakdown
    matched_roles: List[str] = Field(default=[], description="Roles found in common")
    matched_skills: List[str] = Field(default=[], description="Technologies matched directly or canonically")
    matched_domains: List[str] = Field(default=[], description="Domains or industries matched")
    matched_preferences: List[str] = Field(default=[], description="Matched logistical preferences (e.g. Remote)")
    unmatched_requirements: List[str] = Field(default=[], description="Critical missing technologies or stack gaps")
    explanation: str = Field(default="", description="Concise explainable rationale of why this match succeeded or failed")

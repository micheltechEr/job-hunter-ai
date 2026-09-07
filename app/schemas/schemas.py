from pydantic import BaseModel, HttpUrl, Field
from typing import List, Optional
from datetime import datetime

# ----------------- LLM Structured Parsing Schemas -----------------

class ExperienceParsed(BaseModel):
    company: str
    role: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None
    skills_used: Optional[List[str]] = []


class ProjectParsed(BaseModel):
    name: str
    description: Optional[str] = None
    technologies: Optional[List[str]] = []
    url: Optional[str] = None


class ParsedResumeSchema(BaseModel):
    name: str = Field(description="Nome do profissional")
    education: Optional[str] = Field(None, description="Formação acadêmica resumida")
    desired_roles: List[str] = Field(default=[], description="Cargos de interesse inferidos")
    technologies: List[str] = Field(default=[], description="Linguagens de programação e frameworks")
    languages: List[str] = Field(default=[], description="Idiomas e proficiência")
    cloud_providers: List[str] = Field(default=[], description="Provedores de cloud (AWS, Azure, GCP, etc.)")
    devops_tools: List[str] = Field(default=[], description="Ferramentas de DevOps (Docker, Kubernetes, Git, CI/CD)")
    databases: List[str] = Field(default=[], description="Bancos de dados")
    certifications: List[str] = Field(default=[], description="Certificações obtidas")
    location: Optional[str] = Field(None, description="Cidade, Estado e País de residência")
    work_mode: Optional[str] = Field("Remote", description="Modalidade preferida: Remote, Hybrid, On-site")
    professional_goals: Optional[str] = Field(None, description="Objetivos profissionais declarados ou inferidos")
    experiences: List[ExperienceParsed] = Field(default=[], description="Histórico de empregos")
    projects: List[ProjectParsed] = Field(default=[], description="Projetos relevantes listados")


# ----------------- API Request / Response Schemas -----------------

class ResumeResponse(BaseModel):
    id: int
    filename: str
    file_path: str
    version_name: str
    file_hash: str
    parsed_data: Optional[ParsedResumeSchema] = None
    created_at: datetime

    class Config:
        from_attributes = True


class JobCreate(BaseModel):
    title: str
    company: str
    url: Optional[str] = None
    description: str
    location: Optional[str] = None
    work_mode: Optional[str] = None
    salary: Optional[str] = None


class JobAnalysisResponse(BaseModel):
    extracted_role: Optional[str]
    seniority: Optional[str]
    location: Optional[str]
    work_mode: Optional[str]
    required_skills: Optional[List[str]]
    nice_to_have: Optional[List[str]]
    responsibilities: Optional[List[str]]
    education: Optional[List[str]]
    languages: Optional[List[str]]
    experience_required: Optional[str]

    class Config:
        from_attributes = True


class ApplicationResponse(BaseModel):
    id: int
    job_id: int
    resume_id: Optional[int] = None
    recipient_email: Optional[str] = None
    score: Optional[int] = None
    fit: Optional[str] = None
    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    status: str
    created_at: datetime
    sent_at: Optional[datetime] = None
    response_at: Optional[datetime] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class JobResponse(BaseModel):
    id: int
    title: str
    company: str
    url: Optional[str] = None
    description: str
    location: Optional[str] = None
    work_mode: Optional[str] = None
    salary: Optional[str] = None
    created_at: datetime
    analysis: Optional[JobAnalysisResponse] = None
    application: Optional[ApplicationResponse] = None

    class Config:
        from_attributes = True


class MatchResponse(BaseModel):
    score: int
    fit: str # IGNORE, REVIEW, GOOD_MATCH, HIGH_MATCH
    matched_requirements: List[str]
    missing_requirements: List[str]
    strengths: List[str]
    risks: List[str]
    recommendation: bool
    explanation: str
    recommended_resume_id: Optional[int] = None
    recommended_resume_name: Optional[str] = None


class EmailAccountResponse(BaseModel):
    id: int
    email_address: str
    is_active: bool

    class Config:
        from_attributes = True

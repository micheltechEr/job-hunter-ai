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
    seniority_level: Optional[str] = Field("Junior", description="Nível de senioridade inferido estritamente do histórico de experiências (ex: Estagio, Junior, Pleno, Senior)")
    years_of_experience: Optional[float] = Field(0.0, description="Total estimado de anos de experiência profissional com base nas datas de empregos")
    professional_goals: Optional[str] = Field(None, description="Objetivos profissionais declarados ou inferidos")
    experiences: List[ExperienceParsed] = Field(default=[], description="Histórico de empregos")
    projects: List[ProjectParsed] = Field(default=[], description="Projetos relevantes listados")


class TailoredExperience(BaseModel):
    company: str = Field(description="Nome da empresa")
    role: str = Field(description="Cargo ocupado")
    period: str = Field(description="Período de atuação ex: 04/2024 - Presente")
    highlights: List[str] = Field(description="Realizações e bullet points de impacto adaptados para a vaga")
    technologies: List[str] = Field(default=[], description="Stack e ferramentas chave usadas")


class TailoredProject(BaseModel):
    name: str = Field(description="Nome do projeto")
    description: str = Field(description="Descrição de impacto com foco nos requisitos da vaga")
    technologies: List[str] = Field(default=[], description="Tecnologias utilizadas")
    url: Optional[str] = Field(None, description="Link do projeto se houver")


class TailoredResumeSchema(BaseModel):
    name: str = Field(description="Nome completo do candidato")
    target_role: str = Field(description="Título profissional direcionado à vaga alvo")
    contact_info: Optional[str] = Field(None, description="Resumo de contato (localização, links profissionais)")
    summary: str = Field(description="Resumo executivo profissional adaptado para a vaga alvo")
    top_skills: List[str] = Field(description="Competências técnicas essenciais ordenadas por relevância para a vaga")
    secondary_skills: List[str] = Field(default=[], description="Outras ferramentas e tecnologias dominadas")
    experiences: List[TailoredExperience] = Field(description="Experiências profissionais mais relevantes adaptadas")
    projects: List[TailoredProject] = Field(default=[], description="Projetos destacados")
    education: Optional[str] = Field(None, description="Formação acadêmica")
    languages: List[str] = Field(default=[], description="Idiomas falados")


class CopyThiefReport(BaseModel):
    score: int = Field(default=85, description="Score de qualidade de copy de 0 a 100")
    grade: str = Field(default="A", description="Classificação A+, A, B, C")
    summary: str = Field(default="", description="Diagnóstico da copy do currículo")
    action_verbs_count: int = Field(default=0, description="Quantidade de verbos de ação e impacto")
    metrics_count: int = Field(default=0, description="Métricas e números quantificáveis detectados")
    slop_words_detected: List[str] = Field(default=[], description="Palavras clichês / AI slop encontradas")
    strengths: List[str] = Field(default=[], description="Pontos fortes da copy")
    improvements: List[str] = Field(default=[], description="Oportunidades de melhoria de conversão")


class TailorResumeRequest(BaseModel):
    include_seniority: bool = Field(default=False, description="Se False, remove sufixos/prefixos de senioridade (Jr, Pleno, Sr) dos cargos e títulos.")


# ----------------- API Request / Response Schemas -----------------

class ResumeResponse(BaseModel):
    id: int
    filename: str
    file_path: str
    version_name: str
    file_hash: str
    parsed_data: Optional[dict] = None
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


class ScrapeTriggerRequest(BaseModel):
    role: Optional[str] = Field(None, description="Cargo ou palavra-chave principal para buscar (ex: 'Desenvolvedor Python')")
    roles: Optional[List[str]] = Field(default=None, description="Lista de múltiplos cargos para buscar")
    location: Optional[str] = Field(default="Brasil", description="Localização geográfica da busca")
    seniority: Optional[str] = Field(default=None, description="Filtro de senioridade (ex: 'Junior', 'Pleno', 'Junior/Pleno', 'Senior', 'All')")
    platforms: Optional[List[str]] = Field(default=["linkedin", "gupy", "programathor", "indeed", "infojobs", "trabalhabrasil"], description="Plataformas a consultar")
    limit_per_platform: Optional[int] = Field(default=5, ge=1, le=20, description="Quantidade de vagas por plataforma")
    exclude_senior: Optional[bool] = Field(default=True, description="Se true e perfil for Junior/Pleno, descarta vagas Senior/Lead")
    save_to_profile: Optional[bool] = Field(default=False, description="Salvar o cargo no perfil do usuário para buscas futuras")


class ScrapeTriggerResponse(BaseModel):
    message: str
    roles: List[str]
    location: str
    platforms: List[str]
    seniority: Optional[str] = None
    status: str = "initiated"


class UpdateProfileRolesRequest(BaseModel):
    desired_roles: List[str] = Field(description="Lista de cargos desejados")
    location: Optional[str] = Field(None, description="Localização padrão do candidato")
    seniority_level: Optional[str] = Field(None, description="Senioridade do candidato (Junior, Pleno, Senior, etc.)")


class UserProfileResponse(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    desired_roles: Optional[List[str]] = []
    technologies: Optional[List[str]] = []
    location: Optional[str] = None
    work_mode: Optional[str] = None
    seniority_level: Optional[str] = None
    years_of_experience: Optional[float] = None
    professional_goals: Optional[str] = None

    class Config:
        from_attributes = True

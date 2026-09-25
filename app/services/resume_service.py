import os
import re
import hashlib
import json
import logging
from typing import Optional, Dict, List, Any
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from sqlalchemy.orm import selectinload

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from app.config import settings
from app.models.db_models import Resume, UserProfile, Experience, Project, Job, JobAnalysis, Application
from app.schemas.schemas import ParsedResumeSchema, TailoredResumeSchema, CopyThiefReport
from app.services.llm_service import llm_service
from app.services.job_analysis import job_analysis_service

logger = logging.getLogger("job_hunter.resume_service")


def strip_seniority(text: str) -> str:
    """Removes Seniority qualifiers like Jr, Júnior, Pleno, Sênior, Sr, II, III, IV, etc. from a role title."""
    if not text:
        return ""
    patterns = [
        r"\btech\s+lead\b",
        r"\b(j[uú]nior|jr\.?)\b",
        r"\b(pleno|pl\.?|mid)\b",
        r"\b(s[êe]nior|sr\.?)\b",
        r"\b(estagi[aá]ri[oa]|est[aá]gio|trainee)\b",
        r"\b(especialista|specialist|lead|staff|principal|head)\b",
        r"\b(i{1,3}|iv|v)\b",
        r"\b(n[íi]vel\s+[0-9ivx]+)\b",
        r"\b(level\s+[0-9ivx]+)\b"
    ]
    cleaned = text
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s*[/|–—\-]\s*[/|–—\-]+\s*", " ", cleaned)
    cleaned = re.sub(r"[\-–—/|]\s*$", "", cleaned.strip())
    cleaned = re.sub(r"^\s*[\-–—/|]", "", cleaned.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—/,|")
    return cleaned if cleaned else text


def evaluate_resume_copy_thief(resume_data: dict, target_job: Optional[Job] = None) -> CopyThiefReport:
    """
    Evaluates CV copy quality using Copy-Thief principles:
    1. Action Verbs: Verifies presence of strong technical action verbs.
    2. Measurable Metrics: Detects quantifiable impact (% latência, volume, tempo, métricas numéricas).
    3. Anti-AI-Slop: Detects and penalizes generic corporate buzzwords.
    4. Keyword Density: Evaluates stack match and ATS keyword density.
    """
    if not resume_data:
        return CopyThiefReport(score=50, grade="C", summary="Dados do currículo vazios.", feedback=["Carregue um currículo para avaliação Copy-Thief."])

    ACTION_VERBS = [
        "desenvolveu", "otimizou", "implementou", "reduziu", "automatizou", "escalou", "arquitetou",
        "integrou", "construiu", "refatorou", "migrou", "configurou", "orquestrou", "estruturou",
        "desenvolvimento", "otimização", "implementação", "automação", "refatoração", "arquitetura",
        "criou", "liderou", "gerenciou", "modelou", "padronizou", "acelerou", "desenvolver", "implementar"
    ]
    
    SLOP_WORDS = [
        "apaixonado", "apaixonada", "busco oportunidade", "proativo", "proativa", "comunicativo",
        "focado em aprender", "trabalho duro", "dedicado", "motivado", "responsável por", "ajudei a"
    ]

    all_texts = []
    summary = resume_data.get("summary", "") or ""
    all_texts.append(summary)

    experiences = resume_data.get("experiences", []) or []
    bullet_points = []
    for exp in experiences:
        if isinstance(exp, dict):
            bullet_points.extend(exp.get("highlights", []) or [])
            if exp.get("description"):
                bullet_points.append(exp["description"])
        elif hasattr(exp, "highlights"):
            bullet_points.extend(exp.highlights or [])
            if hasattr(exp, "description") and exp.description:
                bullet_points.append(exp.description)

    projects = resume_data.get("projects", []) or []
    for proj in projects:
        if isinstance(proj, dict):
            if proj.get("description"):
                bullet_points.append(proj["description"])
        elif hasattr(proj, "description") and proj.description:
            bullet_points.append(proj.description)

    combined_corpus = " ".join(all_texts + bullet_points).lower()

    # 1. Action Verbs Analysis
    action_verbs_found = set()
    for verb in ACTION_VERBS:
        if re.search(r"\b" + re.escape(verb), combined_corpus):
            action_verbs_found.add(verb)
    action_count = len(action_verbs_found)

    # 2. Measurable Metrics Analysis (regex for %, ms, +, números, R$, USD, x)
    metric_matches = re.findall(r"(\b\d+[%xXkKmM]?\b|\b\d+\.\d+\b|\b\+\d+\b|\b\d+\+|\b[0-9]+ms\b|R\$\s*\d+)", " ".join(bullet_points))
    metrics_count = len(metric_matches)

    # 3. Anti-AI-Slop Analysis
    slop_found = []
    for slop in SLOP_WORDS:
        if slop in combined_corpus:
            slop_found.append(slop)

    # Calculate Score
    score = 70
    if action_count >= 5:
        score += 15
    elif action_count >= 2:
        score += 8

    if metrics_count >= 3:
        score += 15
    elif metrics_count >= 1:
        score += 8

    score -= len(slop_found) * 10
    score = max(20, min(98, score))

    grade = "A+" if score >= 90 else "A" if score >= 80 else "B" if score >= 65 else "C"

    strengths = []
    if action_count >= 3:
        strengths.append(f"Alta densidade de verbos de impacto ({action_count} verbos de ação identificados).")
    if metrics_count >= 1:
        strengths.append(f"Evidências quantificáveis presentes ({metrics_count} métricas/números de impacto).")
    if not slop_found:
        strengths.append("Zero clichês corporativos ou AI slop detectado (foco 100% técnico).")

    improvements = []
    if metrics_count < 2:
        improvements.append("Inclua mais métricas mensuráveis nos bullet points (ex: % de redução de latência, volume de requisições, tempo economizado).")
    if action_count < 4:
        improvements.append("Inicie cada bullet point diretamente com verbos de ação fortes (ex: 'Desenvolveu', 'Implementou', 'Reduziu').")
    if slop_found:
        improvements.append(f"Substitua termos genéricos ({', '.join(slop_found)}) por realizações técnicas concretas.")

    diag_summary = f"Copy avaliada com Grade {grade} ({score}/100). {len(strengths)} pontos fortes e {len(improvements)} recomendações de impacto."

    return CopyThiefReport(
        score=score,
        grade=grade,
        summary=diag_summary,
        action_verbs_count=action_count,
        metrics_count=metrics_count,
        slop_words_detected=slop_found,
        strengths=strengths,
        improvements=improvements
    )

def calculate_sha256(content: bytes) -> str:
    """Calculates SHA256 of file bytes."""
    return hashlib.sha256(content).hexdigest()

def extract_text_from_pdf(pdf_path: str) -> str:
    """Reads PDF and extracts all text."""
    try:
        reader = PdfReader(pdf_path)
        text = ""
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text += f"--- Page {i+1} ---\n{page_text}\n"
        if not text.strip():
            raise ValueError("No text could be extracted from this PDF. It might be scanned or empty.")
        return text
    except Exception as e:
        logger.error(f"Failed to read PDF at {pdf_path}: {e}")
        raise e

async def parse_resume_content(text: str) -> ParsedResumeSchema:
    """Invokes LLM with system instructions to turn CV plain text into a structured Pydantic schema."""
    system_prompt = (
        "Você é uma inteligência artificial especialista em RH e recrutamento de tecnologia no Brasil.\n"
        "Seu papel é receber o texto bruto de um currículo e estruturá-lo rigorosamente.\n"
        "Regras fundamentais:\n"
        "1. Extraia e divida as tecnologias e termos nas categorias corretas (cloud_providers, devops_tools, databases, technologies).\n"
        "2. Senioridade Real: Avalie o histórico de cargos (ex: Estágio, Jr, Pleno, Sr). NUNCA classifique como Sênior um candidato com histórico de Estágio ou Júnior. Preencha `seniority_level` ('Estagio', 'Junior', 'Pleno', 'Senior', 'Especialista').\n"
        "3. Anos de Experiência: Calcule a soma aproximada dos períodos de trabalho em `years_of_experience` (float).\n"
        "4. Cargos de Interesse: Em `desired_roles`, liste cargos compativeis com a senioridade real (ex: 'Desenvolvedor Full Stack Júnior', 'Desenvolvedor Python Pleno', etc.).\n"
        "Seja preciso. Não invente nenhuma informação que não esteja contida explicitamente no texto."
    )
    user_prompt = f"Aqui está o texto bruto do currículo profissional:\n\n{text}"
    
    parsed_cv = await llm_service.get_structured_output(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=ParsedResumeSchema
    )
    return parsed_cv

async def update_user_profile_from_parsed_resume(db: AsyncSession, parsed_data: ParsedResumeSchema):
    """Upserts/Seeds the core UserProfile, Experiences, and Projects from the uploaded resume."""
    try:
        # Check if a profile already exists. For simplicity, we assume a single user profile.
        result = await db.execute(select(UserProfile).limit(1))
        existing_profile = result.scalars().first()

        if existing_profile:
            # Update root fields
            existing_profile.name = parsed_data.name
            existing_profile.education = parsed_data.education
            existing_profile.desired_roles = parsed_data.desired_roles
            existing_profile.technologies = parsed_data.technologies
            existing_profile.languages = parsed_data.languages
            existing_profile.cloud_providers = parsed_data.cloud_providers
            existing_profile.devops_tools = parsed_data.devops_tools
            existing_profile.databases = parsed_data.databases
            existing_profile.certifications = parsed_data.certifications
            existing_profile.location = parsed_data.location
            existing_profile.work_mode = parsed_data.work_mode
            existing_profile.seniority_level = parsed_data.seniority_level or "Junior"
            existing_profile.years_of_experience = parsed_data.years_of_experience or 0.0
            existing_profile.professional_goals = parsed_data.professional_goals
            profile = existing_profile
            
            # Clear old experiences and projects to rewrite them fresh
            await db.execute(delete(Experience).where(Experience.user_profile_id == profile.id))
            await db.execute(delete(Project).where(Project.user_profile_id == profile.id))
            await db.flush()
        else:
            profile = UserProfile(
                name=parsed_data.name,
                education=parsed_data.education,
                desired_roles=parsed_data.desired_roles,
                technologies=parsed_data.technologies,
                languages=parsed_data.languages,
                cloud_providers=parsed_data.cloud_providers,
                devops_tools=parsed_data.devops_tools,
                databases=parsed_data.databases,
                certifications=parsed_data.certifications,
                location=parsed_data.location,
                work_mode=parsed_data.work_mode,
                seniority_level=parsed_data.seniority_level or "Junior",
                years_of_experience=parsed_data.years_of_experience or 0.0,
                professional_goals=parsed_data.professional_goals
            )
            db.add(profile)
            await db.flush() # Secure profile.id

        # Insert experiences
        for exp in parsed_data.experiences:
            db_exp = Experience(
                user_profile_id=profile.id,
                company=exp.company,
                role=exp.role,
                start_date=exp.start_date,
                end_date=exp.end_date,
                description=exp.description,
                skills_used=exp.skills_used
            )
            db.add(db_exp)

        # Insert projects
        for proj in parsed_data.projects:
            db_proj = Project(
                user_profile_id=profile.id,
                name=proj.name,
                description=proj.description,
                technologies=proj.technologies,
                url=proj.url
            )
            db.add(db_proj)

        await db.commit()
    except Exception as e:
        logger.error(f"Error seeding user profile from resume: {e}")
        await db.rollback()
        raise e


def build_resume_pdf(data: dict, output_path: str):
    """Generates an elegant, ATS-friendly PDF resume using ReportLab."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1A202C"),
        fontName="Helvetica-Bold",
        spaceAfter=2
    )
    subtitle_style = ParagraphStyle(
        "DocSubTitle",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#2B6CB0"),
        fontName="Helvetica-Bold",
        spaceAfter=4
    )
    contact_style = ParagraphStyle(
        "Contact",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#718096"),
        spaceAfter=8
    )
    section_heading = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#2C5282"),
        fontName="Helvetica-Bold",
        spaceBefore=8,
        spaceAfter=4
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#2D3748"),
        fontName="Helvetica"
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        parent=body_style,
        leftIndent=12,
        firstLineIndent=-12,
        spaceAfter=2
    )

    story = []

    # 1. Header
    story.append(Paragraph(data.get("name", "").upper(), title_style))
    if data.get("target_role"):
        story.append(Paragraph(data["target_role"], subtitle_style))
    if data.get("contact_info"):
        story.append(Paragraph(data["contact_info"], contact_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E0"), spaceAfter=6, spaceBefore=2))

    # 2. Summary
    if data.get("summary"):
        story.append(Paragraph("RESUMO PROFISSIONAL", section_heading))
        story.append(Paragraph(data["summary"], body_style))
        story.append(Spacer(1, 4))

    # 3. Skills
    top_skills = data.get("top_skills", [])
    if top_skills:
        story.append(Paragraph("HABILIDADES & TECNOLOGIAS", section_heading))
        story.append(Paragraph(f"<b>Competências Principais:</b> {', '.join(top_skills)}", body_style))
        if data.get("secondary_skills"):
            story.append(Paragraph(f"<b>Outras Ferramentas:</b> {', '.join(data['secondary_skills'])}", body_style))
        story.append(Spacer(1, 4))

    # 4. Experiences
    experiences = data.get("experiences", [])
    if experiences:
        story.append(Paragraph("EXPERIÊNCIA PROFISSIONAL", section_heading))
        for exp in experiences:
            role = exp.get("role", "")
            comp = exp.get("company", "")
            period = exp.get("period", "")
            header_line = f"<b>{role}</b> &mdash; {comp} <font color=\"#718096\">({period})</font>"
            story.append(Paragraph(header_line, body_style))
            for hl in exp.get("highlights", []):
                story.append(Paragraph(f"&bull; {hl}", bullet_style))
            if exp.get("technologies"):
                techs = ", ".join(exp["technologies"])
                tech_line = f"<i><font color=\"#4A5568\">Tecnologias: {techs}</font></i>"
                story.append(Paragraph(tech_line, ParagraphStyle("ExpTech", parent=body_style, fontSize=8.5, leftIndent=12, spaceAfter=4)))
            story.append(Spacer(1, 4))

    # 5. Projects
    projects = data.get("projects", [])
    if projects:
        story.append(Paragraph("PROJETOS EM DESTAQUE", section_heading))
        for proj in projects:
            p_name = proj.get("name", "")
            p_desc = proj.get("description", "")
            p_techs = ", ".join(proj.get("technologies", []))
            p_text = f"<b>{p_name}</b>: {p_desc}"
            if p_techs:
                p_text += f" <font color=\"#718096\">[{p_techs}]</font>"
            story.append(Paragraph(f"&bull; {p_text}", bullet_style))
        story.append(Spacer(1, 4))

    # 6. Education & Languages
    if data.get("education") or data.get("languages"):
        story.append(Paragraph("FORMAÇÃO & IDIOMAS", section_heading))
        if data.get("education"):
            story.append(Paragraph(f"<b>Formação:</b> {data['education']}", body_style))
        if data.get("languages"):
            story.append(Paragraph(f"<b>Idiomas:</b> {', '.join(data['languages'])}", body_style))

    doc.build(story)


async def generate_tailored_resume_data(
    user_profile: UserProfile,
    job: Job,
    job_analysis: JobAnalysis,
    include_seniority: bool = False
) -> TailoredResumeSchema:
    """Uses LLM to strategically adapt user's authentic profile to target job criteria applying Copy-Thief principles."""
    seniority_rule = (
        "5. SENIORIDADE: Mantenha as designações de nível originais quando aplicável."
        if include_seniority else
        "5. SENIORIDADE: O candidato optou por NÃO exibir sufixos/prefixos de senioridade (Jr, Júnior, Pleno, Sênior, Sr, II, III). No campo `target_role` e nos cargos de cada experiência `role`, NUNCA inclua esses sufixos. Use títulos limpos e universais como 'Desenvolvedor Full Stack', 'Engenheiro de Software', 'Desenvolvedor Backend'."
    )

    system_prompt = (
        "Você é um especialista sênior em ATS (Applicant Tracking System), recrutamento e copywriting técnico de conversão (metodologia Copy-Thief).\n"
        "Seu papel é reescrever e adaptar o currículo do candidato para maximizar o alinhamento com a vaga alvo.\n"
        "DIRETRIZES DE OURO:\n"
        "1. VERACIDADE ESTRITA: Use APENAS tecnologias, empresas, cargos e fatos reais do perfil do candidato. NUNCA invente empregos ou qualificações inexistentes.\n"
        "2. FOCO ESTRATÉGICO & COPY-THIEF: Destaque em primeiro lugar as experiências, projetos e tecnologias mais alinhadas com a vaga. Inicie bullet points com verbos de ação fortes (Desenvolveu, Implementou, Otimizou, Reduziu, Automatizou, Escalou) e quantifique métricas/impacto onde aplicável.\n"
        "3. LINGUAGEM DE IMPACTO & ZERO SLOP: Elimine clichês corporativos vazios ('apaixonado por', 'busco oportunidade', 'proativo'). Foque em entregas técnicas, arquitetura e redução de trabalho manual.\n"
        "4. IDIOMA: Mantenha em Português do Brasil de alto padrão corporativo (ou termos técnicos universais).\n"
        f"{seniority_rule}\n"
    )

    exps_text = "\n".join([
        f"- {e.role} na {e.company} ({e.start_date or ''} a {e.end_date or 'Atual'}): {e.description or ''} [Techs: {', '.join(e.skills_used or [])}]"
        for e in (user_profile.experiences or [])
    ])

    projs_text = "\n".join([
        f"- {p.name}: {p.description or ''} [Techs: {', '.join(p.technologies or [])}]"
        for p in (user_profile.projects or [])
    ])

    user_prompt = f"""
PERFIL BASE DO CANDIDATO:
Nome: {user_profile.name}
Objetivo/Metas: {user_profile.professional_goals}
Formação: {user_profile.education or 'Superior em Tecnologia'}
Localização: {user_profile.location or 'Brasil / Remoto'}
Tecnologias: {', '.join(user_profile.technologies or [])}
Bancos de Dados: {', '.join(user_profile.databases or [])}
DevOps / Ferramentas: {', '.join(user_profile.devops_tools or [])}
Cloud: {', '.join(user_profile.cloud_providers or [])}
Idiomas: {', '.join(user_profile.languages or ['Português (Nativo)'])}

EXPERIÊNCIAS AUTÊNTICAS (WHITELIST):
{exps_text if exps_text else 'Nenhuma experiência formal cadastrada'}

PROJETOS DESTACADOS:
{projs_text if projs_text else 'Nenhum projeto cadastrado'}

VAGA ALVO (TARGET JOB):
Título: {job.title}
Empresa: {job.company}
Modalidade / Local: {job_analysis.work_mode or job.work_mode} | {job_analysis.location or job.location}
Requisitos Obrigatórios: {', '.join(job_analysis.required_skills or [])}
Diferenciais: {', '.join(job_analysis.nice_to_have or [])}
Descrição da Vaga:
{job.description[:2500]}

Gere o currículo personalizado e adaptado em conformidade com o schema.
"""

    tailored_obj = await llm_service.get_structured_output(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=TailoredResumeSchema
    )
    
    # Apply Hard Gate: Programmatic Whitelist Guard (Zero Hallucination Guarantee + Seniority Sanitization)
    guarded_obj = apply_deterministic_hallucination_guard(tailored_obj, user_profile, include_seniority=include_seniority)
    return guarded_obj


def apply_deterministic_hallucination_guard(
    tailored_obj: TailoredResumeSchema,
    user_profile: UserProfile,
    include_seniority: bool = False
) -> TailoredResumeSchema:
    """
    Hard Gate / Epistemic Filter: Ensures 100% verifiability of tailored resumes.
    1. Programmatically drops any experience that does not match an authentic company in user_profile.experiences.
    2. Programmatically drops any project not matching user_profile.projects.
    3. Guarantees canonical user info, canonical dates and fallback safety.
    4. Programmatically strips seniority qualifiers from role titles when include_seniority is False.
    """
    # 1. Company Whitelist Filter
    valid_experiences = []
    auth_exps = user_profile.experiences or []

    for gen_exp in tailored_obj.experiences:
        gen_comp = (gen_exp.company or "").strip().lower()
        matched_auth = None
        for auth_exp in auth_exps:
            auth_comp = (auth_exp.company or "").strip().lower()
            if auth_comp in gen_comp or gen_comp in auth_comp:
                matched_auth = auth_exp
                break

        if matched_auth:
            gen_exp.company = matched_auth.company
            if not gen_exp.period or gen_exp.period in ("N/A", "", "None"):
                gen_exp.period = f"{matched_auth.start_date or ''} - {matched_auth.end_date or 'Atual'}".strip(" - ")

            # Seniority sanitization on experience role
            if not include_seniority and gen_exp.role:
                gen_exp.role = strip_seniority(gen_exp.role)

            valid_experiences.append(gen_exp)
        else:
            logger.warning(f"Deterministic Guard BLOCKED hallucinated experience: '{gen_exp.company}' ({gen_exp.role})")

    # Fallback to authentic records if LLM dropped everything
    if not valid_experiences and auth_exps:
        for auth_exp in auth_exps:
            clean_role = strip_seniority(auth_exp.role) if not include_seniority else auth_exp.role
            valid_experiences.append(
                TailoredExperience(
                    company=auth_exp.company,
                    role=clean_role,
                    period=f"{auth_exp.start_date or ''} - {auth_exp.end_date or 'Atual'}".strip(" - "),
                    highlights=[auth_exp.description] if auth_exp.description else ["Atuação técnica e desenvolvimento de software."],
                    technologies=auth_exp.skills_used or []
                )
            )

    tailored_obj.experiences = valid_experiences

    # 2. Seniority sanitization on main target role
    if not include_seniority and tailored_obj.target_role:
        tailored_obj.target_role = strip_seniority(tailored_obj.target_role)

    # 2. Project Whitelist Filter
    auth_projs = user_profile.projects or []
    if auth_projs:
        valid_projects = []
        auth_proj_names = [(p.name or "").strip().lower() for p in auth_projs]
        for gen_proj in tailored_obj.projects:
            gen_pname = (gen_proj.name or "").strip().lower()
            if any(ap in gen_pname or gen_pname in ap for ap in auth_proj_names if ap):
                valid_projects.append(gen_proj)
            else:
                logger.warning(f"Deterministic Guard BLOCKED unverified project: '{gen_proj.name}'")
        tailored_obj.projects = valid_projects
    else:
        tailored_obj.projects = []

    # 3. Canonical metadata enforcement
    tailored_obj.name = user_profile.name
    if not tailored_obj.education and user_profile.education:
        tailored_obj.education = user_profile.education
    if not tailored_obj.languages and user_profile.languages:
        tailored_obj.languages = user_profile.languages

    return tailored_obj


async def tailor_and_save_resume_for_job(db: AsyncSession, job_id: int, include_seniority: bool = False) -> Resume:
    """Adapts candidate CV to a specific job, evaluates copy quality via Copy-Thief, generates ATS PDF, persists it and attaches to application."""
    # 1. Fetch Job with relations
    res_job = await db.execute(
        select(Job)
        .options(selectinload(Job.analysis), selectinload(Job.application))
        .where(Job.id == job_id)
    )
    job = res_job.scalars().first()
    if not job:
        raise ValueError(f"Vaga id {job_id} não encontrada.")

    # 2. Fetch User Profile
    res_prof = await db.execute(
        select(UserProfile)
        .options(selectinload(UserProfile.experiences), selectinload(UserProfile.projects))
        .limit(1)
    )
    user_prof = res_prof.scalars().first()
    if not user_prof:
        raise ValueError("Perfil do usuário não configurado.")

    # 3. Ensure Job Analysis exists
    if not job.analysis:
        analysis_data = await job_analysis_service.analyze_job_description(job.description)
        job_analysis = JobAnalysis(
            job_id=job.id,
            extracted_role=analysis_data.extracted_role,
            seniority=analysis_data.seniority,
            location=analysis_data.location,
            work_mode=analysis_data.work_mode,
            required_skills=analysis_data.required_skills,
            nice_to_have=analysis_data.nice_to_have,
            responsibilities=analysis_data.responsibilities,
            education=analysis_data.education,
            languages=analysis_data.languages,
            experience_required=analysis_data.experience_required,
            raw_json=analysis_data.model_dump() if hasattr(analysis_data, "model_dump") else analysis_data.dict()
        )
        db.add(job_analysis)
        await db.commit()
        await db.refresh(job)
    else:
        job_analysis = job.analysis

    # 4. Generate structured tailored content via LLM with Copy-Thief guidelines and Seniority toggle
    tailored_data = await generate_tailored_resume_data(user_prof, job, job_analysis, include_seniority=include_seniority)
    tailored_dict = tailored_data.model_dump() if hasattr(tailored_data, "model_dump") else tailored_data.dict()

    # 5. Evaluate copy quality using Copy-Thief
    copy_report = evaluate_resume_copy_thief(tailored_dict, job)
    tailored_dict["copy_thief_report"] = copy_report.model_dump() if hasattr(copy_report, "model_dump") else copy_report.dict()

    # 6. Build tailored PDF on disk
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    version_name = f"tailored_job_{job_id}"
    safe_title = "".join(c for c in (job.title or "job") if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    filename = f"Curriculo_{safe_title}_{job_id}.pdf"
    file_path = os.path.join(settings.UPLOAD_DIR, f"{version_name}_{filename}")

    build_resume_pdf(tailored_dict, file_path)

    # 7. Read hash
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    file_hash = calculate_sha256(file_bytes)

    # 8. Persist or Update Resume record
    res_existing = await db.execute(select(Resume).where(Resume.version_name == version_name))
    existing_resume = res_existing.scalars().first()

    if existing_resume:
        existing_resume.filename = filename
        existing_resume.file_path = file_path
        existing_resume.file_hash = file_hash
        existing_resume.parsed_data = tailored_dict
        resume_record = existing_resume
    else:
        resume_record = Resume(
            filename=filename,
            file_path=file_path,
            version_name=version_name,
            file_hash=file_hash,
            parsed_data=tailored_dict
        )
        db.add(resume_record)

    await db.flush()

    # 9. Attach to Application
    res_app = await db.execute(select(Application).where(Application.job_id == job_id))
    app_record = res_app.scalars().first()
    if app_record:
        app_record.resume_id = resume_record.id
    else:
        app_record = Application(
            job_id=job_id,
            resume_id=resume_record.id,
            status="REVIEW"
        )
        db.add(app_record)

    await db.commit()
    await db.refresh(resume_record)
    logger.info(f"Tailored resume generated for job {job_id}: {filename} (Resume ID: {resume_record.id}) - Copy-Thief: {copy_report.score}/100")
    return resume_record

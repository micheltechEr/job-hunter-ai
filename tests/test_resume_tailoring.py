import os
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.resume_service import build_resume_pdf, generate_tailored_resume_data
from app.schemas.schemas import TailoredResumeSchema, TailoredExperience, TailoredProject
from app.models.db_models import UserProfile, Job, JobAnalysis

class TestResumeTailoring(unittest.IsolatedAsyncioTestCase):
    def test_build_resume_pdf(self):
        output_path = "tests/temp_test_tailored_resume.pdf"
        data = {
            "name": "Candidato Teste",
            "target_role": "Desenvolvedor Backend Python",
            "contact_info": "Remoto | Brasil | email@teste.com",
            "summary": "Profissional experiente em APIs e microsserviços escaláveis.",
            "top_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
            "secondary_skills": ["Redis", "Git", "CI/CD"],
            "experiences": [
                {
                    "company": "Tech Solutions",
                    "role": "Engenheiro de Software",
                    "period": "2023 - Presente",
                    "highlights": [
                        "Desenvolveu APIs de alto rendimento com FastAPI.",
                        "Otimizou queries PostgreSQL reduzindo latência em 40%."
                    ],
                    "technologies": ["Python", "FastAPI", "PostgreSQL"]
                }
            ],
            "projects": [
                {
                    "name": "Job Hunter",
                    "description": "Plataforma de inteligência para vagas.",
                    "technologies": ["Python", "FastAPI"]
                }
            ],
            "education": "Ciência da Computação",
            "languages": ["Português (Nativo)", "Inglês (Avançado)"]
        }

        build_resume_pdf(data, output_path)
        self.assertTrue(os.path.exists(output_path))
        self.assertGreater(os.path.getsize(output_path), 1000)

        # Cleanup
        if os.path.exists(output_path):
            os.remove(output_path)

    async def test_generate_tailored_resume_data(self):
        mock_tailored = TailoredResumeSchema(
            name="Candidato Teste",
            target_role="Full Stack Engineer (React/Node)",
            contact_info="Remoto | Brasil",
            summary="Especialista em React e Node.js para aplicações escaláveis.",
            top_skills=["React", "TypeScript", "Node.js"],
            secondary_skills=["Docker", "PostgreSQL"],
            experiences=[
                TailoredExperience(
                    company="Empresa X",
                    role="Dev Full Stack",
                    period="2022 - Atual",
                    highlights=["Entregou frontend responsivo e API REST."],
                    technologies=["React", "Node.js"]
                )
            ],
            projects=[
                TailoredProject(
                    name="Projeto Alpha",
                    description="Sistema web integrado.",
                    technologies=["React", "TypeScript"]
                )
            ],
            education="Sistemas de Informação",
            languages=["Português"]
        )

        with patch("app.services.resume_service.llm_service.get_structured_output", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_tailored

            dummy_user = UserProfile(
                name="Candidato Teste",
                professional_goals="Desenvolvedor",
                technologies=["React", "Node.js"],
                experiences=[],
                projects=[]
            )
            dummy_job = Job(
                title="Full Stack Developer",
                company="Empresa X",
                description="Vaga de Full Stack React e Node."
            )
            dummy_analysis = JobAnalysis(
                extracted_role="Full Stack Developer",
                required_skills=["React", "Node.js"]
            )

            res = await generate_tailored_resume_data(dummy_user, dummy_job, dummy_analysis)
            self.assertEqual(res.target_role, "Full Stack Engineer (React/Node)")
            self.assertEqual(len(res.top_skills), 3)
            self.assertEqual(mock_llm.call_count, 1)

    def test_deterministic_guard_blocks_hallucination(self):
        from app.services.resume_service import apply_deterministic_hallucination_guard
        from app.models.db_models import Experience

        user_profile = UserProfile(
            name="Ângelo Miguel",
            experiences=[
                Experience(company="Varejo Hub", role="Dev Full Stack", start_date="04/2024", end_date="Presente"),
                Experience(company="Wellon Digital", role="Técnico", start_date="08/2022", end_date="03/2024"),
                Experience(company="Plugoo", role="Estagiário", start_date="01/2021", end_date="04/2022")
            ],
            projects=[]
        )

        hallucinated_schema = TailoredResumeSchema(
            name="Ângelo Miguel",
            target_role="Full Stack AI",
            summary="Resumo",
            top_skills=["Python"],
            secondary_skills=[],
            experiences=[
                TailoredExperience(
                    company="Solis Educacional",  # Hallucinated!
                    role="Dev React Native",
                    period="2025",
                    highlights=["Criou app Solis"],
                    technologies=["React Native"]
                ),
                TailoredExperience(
                    company="Varejo Hub",  # Authentic!
                    role="Dev Full Stack Jr",
                    period="2024 - Presente",
                    highlights=["Criou agentes de IA"],
                    technologies=["Node.js", "React"]
                ),
                TailoredExperience(
                    company="NTT DATA Europe",  # Hallucinated!
                    role="Analista",
                    period="2026",
                    highlights=["ServiceNow"],
                    technologies=["ServiceNow"]
                )
            ],
            projects=[]
        )

        guarded = apply_deterministic_hallucination_guard(hallucinated_schema, user_profile)
        
        # Must only contain the authentic company "Varejo Hub"
        self.assertEqual(len(guarded.experiences), 1)
        self.assertEqual(guarded.experiences[0].company, "Varejo Hub")
        # When include_seniority=False (default), seniority "Jr" must be stripped from role
        self.assertEqual(guarded.experiences[0].role, "Dev Full Stack")

    def test_strip_seniority_utility(self):
        from app.services.resume_service import strip_seniority
        self.assertEqual(strip_seniority("Desenvolvedor Full Stack Jr"), "Desenvolvedor Full Stack")
        self.assertEqual(strip_seniority("Desenvolvedor Python Júnior"), "Desenvolvedor Python")
        self.assertEqual(strip_seniority("Engenheiro Backend Pleno"), "Engenheiro Backend")
        self.assertEqual(strip_seniority("Tech Lead / Sênior Python Engineer"), "Python Engineer")
        self.assertEqual(strip_seniority("Técnico em Informática Jr II"), "Técnico em Informática")
        self.assertEqual(strip_seniority("Desenvolvedor Frontend"), "Desenvolvedor Frontend")

    def test_copy_thief_evaluation(self):
        from app.services.resume_service import evaluate_resume_copy_thief
        sample_cv = {
            "name": "Ângelo Miguel",
            "summary": "Desenvolvedor focado em arquitetura de microsserviços e automação com agentes de IA.",
            "experiences": [
                {
                    "company": "Varejo Hub",
                    "role": "Desenvolvedor Full Stack",
                    "highlights": [
                        "Desenvolveu APIs REST de alto rendimento reduzindo latência em 40%.",
                        "Implementou cache Redis atendendo +50.000 requisições diárias.",
                        "Automatizou pipelines de RAG com Supabase e OpenAI."
                    ]
                }
            ],
            "projects": [
                {
                    "name": "AI Job Hunter",
                    "description": "Desenvolveu sistema autônomo com IA acelerando candidaturas em 80%."
                }
            ]
        }

        report = evaluate_resume_copy_thief(sample_cv)
        self.assertGreaterEqual(report.score, 80)
        self.assertIn(report.grade, ["A+", "A"])
        self.assertGreater(report.action_verbs_count, 0)
        self.assertGreater(report.metrics_count, 0)
        self.assertEqual(len(report.slop_words_detected), 0)
        self.assertGreater(len(report.strengths), 0)

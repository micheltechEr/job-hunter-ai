import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.scheduler import run_job_hunting_scrape
from app.schemas.schemas import ScrapeTriggerRequest, ScrapeTriggerResponse, UpdateProfileRolesRequest


class TestScrapeByRole(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_trigger_schema_validation(self):
        req = ScrapeTriggerRequest(
            role="Engenheiro de IA, Desenvolvedor Python",
            location="Remoto",
            platforms=["linkedin", "gupy"],
            limit_per_platform=3,
            save_to_profile=True
        )
        self.assertEqual(req.role, "Engenheiro de IA, Desenvolvedor Python")
        self.assertEqual(req.location, "Remoto")
        self.assertEqual(req.platforms, ["linkedin", "gupy"])
        self.assertEqual(req.limit_per_platform, 3)
        self.assertTrue(req.save_to_profile)

    async def test_run_job_hunting_scrape_with_custom_roles(self):
        with patch("app.services.scraper_service.scraper_service.scrape_linkedin_jobs", new_callable=AsyncMock) as mock_li, \
             patch("app.services.scraper_service.scraper_service.scrape_linkedin_posts", new_callable=AsyncMock) as mock_li_posts, \
             patch("app.services.scraper_service.scraper_service.scrape_gupy_jobs", new_callable=AsyncMock) as mock_gp, \
             patch("app.services.scraper_service.scraper_service.scrape_programathor_jobs", new_callable=AsyncMock) as mock_pt, \
             patch("app.services.scraper_service.scraper_service.scrape_indeed_jobs", new_callable=AsyncMock) as mock_indeed, \
             patch("app.services.scraper_service.scraper_service.scrape_infojobs_jobs", new_callable=AsyncMock) as mock_infojobs, \
             patch("app.services.scraper_service.scraper_service.scrape_trabalhabrasil_jobs", new_callable=AsyncMock) as mock_tb, \
             patch("app.services.scraper_service.scraper_service.scrape_catho_jobs", new_callable=AsyncMock) as mock_catho, \
             patch("app.services.scraper_service.scraper_service.ingest_new_jobs", new_callable=AsyncMock) as mock_ingest, \
             patch("app.services.scheduler.AsyncSessionLocal") as mock_session_local:

            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.first.return_value = None
            mock_session.execute.return_value = mock_result
            mock_session_local.return_value.__aenter__.return_value = mock_session

            mock_li.return_value = [{"title": "Python Dev", "company": "Tech Corp", "url": "https://li.com/1", "description": "Python dev"}]
            mock_li_posts.return_value = [{"title": "Python Post", "company": "Recruiter", "url": "https://li.com/post/1", "description": "Vaga aberta", "recipient_email": "rh@empresa.com"}]
            mock_gp.return_value = []
            mock_pt.return_value = []
            mock_indeed.return_value = [{"title": "Python Dev Indeed", "company": "Indeed Corp", "url": "https://indeed.com/1", "description": "Python dev"}]
            mock_infojobs.return_value = []
            mock_tb.return_value = []
            mock_catho.return_value = [{"title": "Python Dev Catho", "company": "Catho Corp", "url": "https://catho.com.br/vagas/1", "description": "Python dev"}]

            result = await run_job_hunting_scrape(
                roles=["Desenvolvedor Python"],
                location="Remoto",
                platforms=["linkedin", "linkedin_posts", "indeed"],
                limit_per_platform=2,
                save_to_profile=False
            )

            self.assertEqual(result["roles"], ["Desenvolvedor Python"])
            self.assertEqual(result["location"], "Remoto")
            self.assertEqual(result["platforms"], ["linkedin", "linkedin_posts", "indeed"])
            self.assertEqual(mock_li.call_args[1]["location"], "Remoto")
            self.assertEqual(mock_li_posts.call_args[1]["location"], "Remoto")
            self.assertEqual(mock_indeed.call_args[1]["location"], "Remoto")
            self.assertEqual(mock_li.call_count, 1)
            self.assertEqual(mock_li_posts.call_count, 1)
            self.assertEqual(mock_indeed.call_count, 1)
            self.assertEqual(mock_gp.call_count, 0)
            self.assertEqual(mock_pt.call_count, 0)
            self.assertEqual(mock_infojobs.call_count, 0)
            self.assertEqual(mock_tb.call_count, 0)
            self.assertEqual(mock_catho.call_count, 0)
            self.assertEqual(mock_ingest.call_count, 3)

    async def test_update_profile_roles_schema(self):
        req = UpdateProfileRolesRequest(
            desired_roles=["Tech Lead Python", "Cloud Architect"],
            location="Remoto",
            seniority_level="Pleno"
        )
        self.assertEqual(len(req.desired_roles), 2)
        self.assertEqual(req.location, "Remoto")
        self.assertEqual(req.seniority_level, "Pleno")

    def test_is_senior_title_detection(self):
        from app.services.scraper_service import is_senior_title
        self.assertTrue(is_senior_title("Desenvolvedor Backend Sênior"))
        self.assertTrue(is_senior_title("Senior React Native Developer"))
        self.assertTrue(is_senior_title("Engenheiro de Software Sr."))
        self.assertTrue(is_senior_title("Tech Lead / Staff Engineer"))
        self.assertTrue(is_senior_title("Especialista de Dados"))
        self.assertTrue(is_senior_title("Head of Engineering"))

        # Junior / Pleno / General roles should NOT be classified as Senior
        self.assertFalse(is_senior_title("Desenvolvedor Full Stack Jr"))
        self.assertFalse(is_senior_title("Desenvolvedor Python Júnior"))
        self.assertFalse(is_senior_title("Programador Pleno"))
        self.assertFalse(is_senior_title("Estagiário de TI"))
        self.assertFalse(is_senior_title("Desenvolvedor Web"))

    def test_is_location_matching(self):
        from app.services.scraper_service import is_location_matching

        # 1. Target: Salvador, BA
        self.assertTrue(is_location_matching(job_location="Salvador, Bahia", target_location="Salvador, BA", work_mode="On-site"))
        self.assertTrue(is_location_matching(job_location="Salvador - BA", target_location="Salvador", work_mode="Hybrid"))
        self.assertTrue(is_location_matching(job_location="Brasil", target_location="Salvador, BA", work_mode="Remote"))
        self.assertTrue(is_location_matching(job_location="Remoto", target_location="Salvador, BA", work_mode="Remote"))
        self.assertFalse(is_location_matching(job_location="São Paulo, SP", target_location="Salvador, BA", work_mode="On-site"))
        self.assertFalse(is_location_matching(job_location="Curitiba, PR", target_location="Salvador, BA", work_mode="Hybrid"))
        self.assertFalse(is_location_matching(job_location="Belo Horizonte, MG", target_location="Salvador, BA", work_mode="On-site"))

        # 2. Target: Remoto
        self.assertTrue(is_location_matching(job_location="Remoto", target_location="Remoto", work_mode="Remote"))
        self.assertTrue(is_location_matching(job_location="Brasil", target_location="Remoto", work_mode="Remote"))
        self.assertFalse(is_location_matching(job_location="São Paulo, SP", target_location="Remoto", work_mode="On-site"))

        # 3. Target: Brasil
        self.assertTrue(is_location_matching(job_location="São Paulo, SP", target_location="Brasil", work_mode="On-site"))
        self.assertTrue(is_location_matching(job_location="Salvador, BA", target_location="Brasil", work_mode="On-site"))
        self.assertFalse(is_location_matching(job_location="New York, USA", target_location="Brasil", work_mode="On-site"))

    def test_is_role_relevant_semantic_domain_gating(self):
        from app.services.scraper_service import is_role_relevant

        # Target: Analista de Dados
        self.assertTrue(is_role_relevant("Analista de Dados Júnior", "Analista de Dados"))
        self.assertTrue(is_role_relevant("Data Analyst Pleno", "Analista de Dados"))
        self.assertTrue(is_role_relevant("Analista de Business Intelligence (BI)", "Analista de Dados"))
        self.assertTrue(is_role_relevant("Analista de Power BI e SQL", "Analista de Dados"))
        self.assertTrue(is_role_relevant("Analytics Engineer", "Analista de Dados"))

        # False positives from operational/unrelated roles must be REJECTED
        self.assertFalse(is_role_relevant("Analista de RM", "Analista de Dados"))
        self.assertFalse(is_role_relevant("Analista de Qualidade", "Analista de Dados"))
        self.assertFalse(is_role_relevant("Analista de EHG", "Analista de Dados"))
        self.assertFalse(is_role_relevant("Analista de ESG", "Analista de Dados"))
        self.assertFalse(is_role_relevant("Analista de Operações", "Analista de Dados"))
        self.assertFalse(is_role_relevant("Analista de Suporte Técnico", "Analista de Dados"))
        self.assertFalse(is_role_relevant("Analista Financeiro", "Analista de Dados"))
        self.assertFalse(is_role_relevant("Analista de Logística", "Analista de Dados"))

        # Target: Desenvolvedor Full Stack
        self.assertTrue(is_role_relevant("Desenvolvedor Full Stack Jr", "Desenvolvedor Full Stack"))
        self.assertTrue(is_role_relevant("Python Developer", "Desenvolvedor Full Stack"))
        self.assertFalse(is_role_relevant("Analista de RM", "Desenvolvedor Full Stack"))
        self.assertFalse(is_role_relevant("Analista de Operações", "Desenvolvedor Full Stack"))

    def test_parse_location_clean_separation(self):
        from app.services.scraper_service import parse_location

        # 1. City without state
        loc_fsa = parse_location("Feira de Santana")
        self.assertFalse(loc_fsa["is_national"])
        self.assertEqual(loc_fsa["city"], "Feira de Santana")
        self.assertEqual(loc_fsa["clean_loc"], "Feira de Santana")
        self.assertEqual(loc_fsa["slug"], "feira-de-santana")

        # 2. City with state abbreviation
        loc_fsa_ba = parse_location("Feira de Santana, BA")
        self.assertFalse(loc_fsa_ba["is_national"])
        self.assertEqual(loc_fsa_ba["city"], "Feira de Santana")
        self.assertEqual(loc_fsa_ba["state"], "BA")
        self.assertEqual(loc_fsa_ba["clean_loc"], "Feira de Santana, BA")

        # 3. National / Remote defaults
        loc_br = parse_location("Brasil")
        self.assertTrue(loc_br["is_national"])
        self.assertEqual(loc_br["clean_loc"], "Brasil")

        loc_none = parse_location("Não informado")
        self.assertTrue(loc_none["is_national"])
        self.assertEqual(loc_none["clean_loc"], "Brasil")

    def test_infojobs_poblacion_id_resolution(self):
        from app.services.scraper_service import get_infojobs_poblacion_id

        # 1. Feira de Santana
        pid_fsa = get_infojobs_poblacion_id("Feira de Santana")
        self.assertEqual(pid_fsa, "5202596")

        # 2. Salvador
        pid_ssa = get_infojobs_poblacion_id("Salvador")
        self.assertEqual(pid_ssa, "5202974")

        # 3. Empty returns empty
        self.assertEqual(get_infojobs_poblacion_id(""), "")

    def test_job_deduplication_helpers(self):
        from app.services.scraper_service import canonicalize_job_url, compute_job_fingerprint

        # 1. URL Canonicalization strips tracking params
        u1 = "https://www.linkedin.com/jobs/view/4123456789?position=1&pageNum=0&refId=abc&utm_source=share"
        u2 = "https://www.linkedin.com/jobs/view/4123456789?position=2&pageNum=1"
        self.assertEqual(canonicalize_job_url(u1), "https://www.linkedin.com/jobs/view/4123456789")
        self.assertEqual(canonicalize_job_url(u1), canonicalize_job_url(u2))

        # 2. Title + Company fingerprinting
        fp1 = compute_job_fingerprint("Desenvolvedor Fullstack Jr", "Vagas MRM Brasil")
        fp2 = compute_job_fingerprint("Desenvolvedor Fullstack Jr.", "Vagas MRM Brasil")
        self.assertEqual(fp1, fp2)

    def test_expired_or_closed_job_detection(self):
        from app.services.scraper_service import is_expired_or_closed_job

        # 1. Title with expiration markers
        self.assertTrue(is_expired_or_closed_job("Vencida Developer Back-End Senior - Node.Js"))
        self.assertTrue(is_expired_or_closed_job("[ENCERRADA] Desenvolvedor Full Stack"))
        self.assertTrue(is_expired_or_closed_job("Vaga Finalizada - Engenheiro de Software"))
        self.assertTrue(is_expired_or_closed_job("Desenvolvedor Java - Inscrições Encerradas"))

        # 2. Card text with expiration badges
        self.assertTrue(is_expired_or_closed_job("Desenvolvedor React", card_text="Vaga vencida há 2 dias"))
        self.assertTrue(is_expired_or_closed_job("Desenvolvedor Node", card_text="Processo seletivo encerrado"))

        # 3. Active valid job
        self.assertFalse(is_expired_or_closed_job("Desenvolvedor Full Stack Júnior", description="Vaga aberta para atuação remota."))
        self.assertFalse(is_expired_or_closed_job("Analista de Sistemas Pleno", description="Venha fazer parte do nosso time."))


if __name__ == "__main__":
    unittest.main()

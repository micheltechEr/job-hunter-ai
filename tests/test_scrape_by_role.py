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
             patch("app.services.scraper_service.scraper_service.scrape_gupy_jobs", new_callable=AsyncMock) as mock_gp, \
             patch("app.services.scraper_service.scraper_service.scrape_programathor_jobs", new_callable=AsyncMock) as mock_pt, \
             patch("app.services.scraper_service.scraper_service.ingest_new_jobs", new_callable=AsyncMock) as mock_ingest, \
             patch("app.services.scheduler.AsyncSessionLocal") as mock_session_local:

            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.first.return_value = None
            mock_session.execute.return_value = mock_result
            mock_session_local.return_value.__aenter__.return_value = mock_session

            mock_li.return_value = [{"title": "Python Dev", "company": "Tech Corp", "url": "https://li.com/1", "description": "Python dev"}]
            mock_gp.return_value = []
            mock_pt.return_value = []

            result = await run_job_hunting_scrape(
                roles=["Desenvolvedor Python"],
                location="Remoto",
                platforms=["linkedin"],
                limit_per_platform=2,
                save_to_profile=False
            )

            self.assertEqual(result["roles"], ["Desenvolvedor Python"])
            self.assertEqual(result["location"], "Remoto")
            self.assertEqual(result["platforms"], ["linkedin"])
            self.assertEqual(mock_li.call_count, 1)
            self.assertEqual(mock_gp.call_count, 0)
            self.assertEqual(mock_pt.call_count, 0)
            self.assertEqual(mock_ingest.call_count, 1)

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

import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.embedding_service import EmbeddingService
from app.services.job_analysis import JobAnalysisService
from app.services.ats_queue import ATSWorkerQueue
from app.schemas.schemas import JobAnalysisResponse

class TestATSAndSearch(unittest.IsolatedAsyncioTestCase):
    async def test_embedding_cache(self):
        service = EmbeddingService()
        service._cache.clear()

        # Mock client embeddings
        mock_resp = MagicMock()
        mock_item = MagicMock()
        mock_item.embedding = [0.1, 0.2, 0.3]
        mock_resp.data = [mock_item]

        with patch.object(service.client.embeddings, "create", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_resp

            vec1 = await service.get_embedding("Desenvolvedor Python Pleno")
            self.assertEqual(vec1, [0.1, 0.2, 0.3])
            self.assertEqual(mock_create.call_count, 1)

            # Second call with same text should hit cache with 0 API calls
            vec2 = await service.get_embedding("Desenvolvedor Python Pleno")
            self.assertEqual(vec2, [0.1, 0.2, 0.3])
            self.assertEqual(mock_create.call_count, 1)

    async def test_job_analysis_cache(self):
        service = JobAnalysisService()
        service._cache.clear()

        dummy_analysis = JobAnalysisResponse(
            extracted_role="Engenheiro de IA",
            seniority="Senior",
            location="Remoto",
            work_mode="Remote",
            required_skills=["Python", "FastAPI", "OpenAI"],
            nice_to_have=["Docker"],
            responsibilities=["Desenvolvimento de modelos"],
            education=["Superior"],
            languages=["Inglês"],
            experience_required="3+ anos"
        )

        with patch("app.services.job_analysis.llm_service.get_structured_output", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = dummy_analysis

            desc = "Vaga para Engenheiro de IA com foco em LLMs e Python."
            res1 = await service.analyze_job_description(desc)
            self.assertEqual(res1.extracted_role, "Engenheiro de IA")
            self.assertEqual(mock_llm.call_count, 1)

            # Second call should return from cache
            res2 = await service.analyze_job_description(desc)
            self.assertEqual(res2.extracted_role, "Engenheiro de IA")
            self.assertEqual(mock_llm.call_count, 1)

    async def test_ats_queue_enqueue(self):
        queue = ATSWorkerQueue(max_concurrency=1)
        self.assertEqual(queue.queue.qsize(), 0)

        await queue.enqueue(101)
        self.assertEqual(queue.queue.qsize(), 1)
        self.assertIn(101, queue.processing_job_ids)

        # Enqueue duplicate should deduplicate
        await queue.enqueue(101)
        self.assertEqual(queue.queue.qsize(), 1)

    async def test_ingest_new_jobs_success(self):
        from app.services.scraper_service import scraper_service
        from app.models.db_models import Job, Application

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_db.execute.return_value = mock_result

        scraped = [{
            "title": "Desenvolvedor Full Stack Pleno",
            "company": "Tech Corp",
            "url": "https://example.com/job/123",
            "description": "Python e React",
            "location": "Remoto",
            "work_mode": "Remote",
            "salary": "N/A"
        }]

        with patch("app.services.ats_queue.ats_worker_queue.enqueue", new_callable=AsyncMock) as mock_enqueue:
            await scraper_service.ingest_new_jobs(mock_db, scraped)
            
            # Verify DB operations
            self.assertEqual(mock_db.add.call_count, 2)
            added_objects = [call[0][0] for call in mock_db.add.call_args_list]
            self.assertTrue(any(isinstance(obj, Job) for obj in added_objects))
            self.assertTrue(any(isinstance(obj, Application) for obj in added_objects))
            
            # Verify newly ingested job is set to DISCOVERED (on-demand ATS)
            app_obj = next(obj for obj in added_objects if isinstance(obj, Application))
            self.assertEqual(app_obj.status, "DISCOVERED")
            self.assertIsNone(app_obj.score)

            # Verify commit called and rollback not called
            self.assertEqual(mock_db.commit.call_count, 1)
            self.assertEqual(mock_db.rollback.call_count, 0)
            # ATS is NOT enqueued automatically (avoids rate limits)
            self.assertEqual(mock_enqueue.call_count, 0)

    async def test_application_draft_cache(self):
        from app.services.application_generator import ApplicationGeneratorService, ApplicationDraftSchema
        from app.models.db_models import UserProfile, Job, JobAnalysis

        service = ApplicationGeneratorService()
        service._draft_cache.clear()

        user = UserProfile(id=1, name="Dev Test", technologies=["Python", "FastAPI"], education="BS", location="Remote", professional_goals="Backend")
        job = Job(id=99, title="Python Lead", company="Stably", description="Looking for Python FastAPI lead engineer.")
        analysis = JobAnalysis(id=10, job_id=99, extracted_role="Python Lead", required_skills=["Python", "FastAPI"])

        dummy_draft = ApplicationDraftSchema(subject="Candidatura Python Lead", body="Olá, tenho interesse na vaga.")

        with patch("app.services.llm_service.llm_service.get_structured_output", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = dummy_draft

            # 1st call: invokes LLM
            res1 = await service.generate_draft(user, job, analysis)
            self.assertEqual(res1.subject, "Candidatura Python Lead")
            self.assertEqual(mock_llm.call_count, 1)

            # 2nd call: cache HIT, 0 LLM calls
            res2 = await service.generate_draft(user, job, analysis)
            self.assertEqual(res2.subject, "Candidatura Python Lead")
            self.assertEqual(mock_llm.call_count, 1)

    async def test_matching_fast_path_and_cache(self):
        from app.services.matching import MatchingService
        from app.models.db_models import UserProfile, Job, JobAnalysis, Resume
        from app.schemas.schemas import MatchResponse

        service = MatchingService()
        service._match_cache.clear()

        user = UserProfile(id=1, name="Dev Test", seniority_level="Senior", technologies=["Python", "FastAPI"], experiences=[])
        job = Job(id=50, title="Python Senior", company="Tech Corp", description="FastAPI e Python")
        analysis = JobAnalysis(id=5, job_id=50, extracted_role="Python Senior", seniority="Senior", required_skills=["Python"])
        resume = Resume(id=10, version_name="CV Backend", parsed_data={"skills": ["Python"]})

        mock_db = AsyncMock()

        dummy_match = MatchResponse(
            score=88,
            fit="HIGH_MATCH",
            matched_requirements=["Python"],
            missing_requirements=[],
            strengths=["Excelente fit"],
            risks=[],
            recommendation=True,
            explanation="Candidato atende todos os requisitos.",
            recommended_resume_id=10,
            recommended_resume_name="CV Backend"
        )

        with patch("app.services.llm_service.llm_service.get_structured_output", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = dummy_match

            # 1st call: invokes LLM and stores in cache
            res1 = await service.match_job_profile(
                db=mock_db,
                job_id=50,
                job=job,
                job_analysis=analysis,
                user_profile=user,
                resumes=[resume]
            )
            self.assertEqual(res1.score, 88)
            self.assertEqual(mock_llm.call_count, 1)

            # 2nd call: fast cache hit
            res2 = await service.match_job_profile(
                db=mock_db,
                job_id=50,
                job=job,
                job_analysis=analysis,
                user_profile=user,
                resumes=[resume]
            )
            self.assertEqual(res2.score, 88)
            self.assertEqual(mock_llm.call_count, 1)

    async def test_matching_seniority_guard_blocks_junior_for_senior_job(self):
        from app.services.matching import MatchingService
        from app.models.db_models import UserProfile, Job, JobAnalysis, Resume
        from app.schemas.schemas import MatchResponse

        service = MatchingService()
        service._match_cache.clear()

        # Junior candidate vs Senior job
        user = UserProfile(id=2, name="Junior Dev", seniority_level="Junior", years_of_experience=1.0, technologies=["Python"], experiences=[])
        job = Job(id=51, title="Tech Lead / Senior Python Engineer", company="BigTech", description="Lead team")
        analysis = JobAnalysis(id=6, job_id=51, extracted_role="Tech Lead", seniority="Senior", required_skills=["Python"])
        resume = Resume(id=11, version_name="CV Junior", parsed_data={"skills": ["Python"]})

        mock_db = AsyncMock()
        dummy_match = MatchResponse(
            score=90,
            fit="HIGH_MATCH",
            matched_requirements=["Python"],
            missing_requirements=[],
            strengths=["Python"],
            risks=[],
            recommendation=True,
            explanation="Excelente stack."
        )

        with patch("app.services.llm_service.llm_service.get_structured_output", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = dummy_match

            res = await service.match_job_profile(
                db=mock_db,
                job_id=51,
                job=job,
                job_analysis=analysis,
                user_profile=user,
                resumes=[resume]
            )
            # Seniority guard must cap score to <= 35 and fit to IGNORE
            self.assertLessEqual(res.score, 35)
            self.assertEqual(res.fit, "IGNORE")
            self.assertFalse(res.recommendation)
            self.assertTrue(any("senioridade" in r.lower() or "sênior" in r.lower() for r in res.risks))

    async def test_rate_limiter_concurrency_no_deadlock(self):
        from app.services.rate_limiter import AsyncTokenBucketRateLimiter
        import time

        limiter = AsyncTokenBucketRateLimiter(requests_per_minute=300, max_concurrency=10)
        
        async def mock_task(idx):
            await limiter.acquire()
            try:
                await asyncio.sleep(0.01)
                return idx
            finally:
                limiter.release()

        t0 = time.monotonic()
        tasks = [mock_task(i) for i in range(25)]
        results = await asyncio.gather(*tasks)
        t1 = time.monotonic()

        self.assertEqual(len(results), 25)
        # All 25 tasks must complete smoothly in less than 2 seconds (no multi-minute compounding delay)
        self.assertLess(t1 - t0, 2.0)

    async def test_on_demand_match_creates_analysis_if_missing(self):
        from app.services.matching import MatchingService
        from app.models.db_models import UserProfile, Job, Resume
        from app.schemas.schemas import MatchResponse, JobAnalysisResponse

        service = MatchingService()
        service._match_cache.clear()

        user = UserProfile(id=1, name="Dev Test", seniority_level="Junior", technologies=["Python", "FastAPI"], experiences=[])
        job = Job(id=60, title="Python Jr Developer", company="Startup Tech", description="Vaga de Python Jr com FastAPI")
        resume = Resume(id=1, version_name="CV Dev", parsed_data={"skills": ["Python"]})

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_result_none = MagicMock()
        mock_result_none.scalars.return_value.first.return_value = None

        mock_db.execute.return_value = mock_result_none

        dummy_analysis = JobAnalysisResponse(
            extracted_role="Python Jr Developer",
            seniority="Junior",
            location="Remoto",
            work_mode="Remote",
            required_skills=["Python"],
            nice_to_have=[],
            responsibilities=[],
            education=[],
            languages=[],
            experience_required="1 ano"
        )

        dummy_match = MatchResponse(
            score=85,
            fit="HIGH_MATCH",
            matched_requirements=["Python"],
            missing_requirements=[],
            strengths=["Fit técnico"],
            risks=[],
            recommendation=True,
            explanation="Excelente fit."
        )

        with patch("app.services.job_analysis.job_analysis_service.analyze_job_description", new_callable=AsyncMock) as mock_analyze, \
             patch("app.services.llm_service.llm_service.get_structured_output", new_callable=AsyncMock) as mock_llm:
            
            mock_analyze.return_value = dummy_analysis
            mock_llm.return_value = dummy_match

            res = await service.match_job_profile(
                db=mock_db,
                job_id=60,
                job=job,
                job_analysis=None,
                user_profile=user,
                resumes=[resume]
            )

            # Analysis must be generated on demand when missing
            self.assertEqual(mock_analyze.call_count, 1)
            self.assertEqual(res.score, 85)
            self.assertEqual(res.fit, "HIGH_MATCH")

    async def test_primary_stack_barrier_guard_disqualifies_missing_language(self):
        from app.services.matching import MatchingService
        from app.models.db_models import UserProfile, Job, JobAnalysis, Resume
        from app.schemas.schemas import MatchResponse

        service = MatchingService()
        service._match_cache.clear()

        # Candidate with JS/Node/PHP stack
        user = UserProfile(
            id=1,
            name="Angelo Dev",
            seniority_level="Junior",
            technologies=["React", "TypeScript", "Node.js", "PHP", "Laravel"],
            experiences=[]
        )

        # Job explicitly requiring Java 8 / Spring Boot
        job = Job(id=304, title="Desenvolvedor Backend Trainee", company="Confitec", description="Vaga Java 8, Spring Boot, Hibernate, SQL")
        job_analysis = JobAnalysis(
            id=15,
            job_id=304,
            extracted_role="Desenvolvedor Backend",
            seniority="Trainee",
            required_skills=["Java 8", "Spring Boot", "Hibernate"]
        )
        resume = Resume(id=1, version_name="CV Base", parsed_data={"skills": ["React", "Node.js"]})

        mock_db = AsyncMock()

        # Mock LLM returning an erroneously high score
        hallucinated_match = MatchResponse(
            score=95,
            fit="HIGH_MATCH",
            matched_requirements=["SQL"],
            missing_requirements=["Java 8"],
            strengths=["3 anos de experiência"],
            risks=[],
            recommendation=True,
            explanation="Excelente fit para trainee."
        )

        with patch("app.services.llm_service.llm_service.get_structured_output", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = hallucinated_match

            res = await service.match_job_profile(
                db=mock_db,
                job_id=304,
                job=job,
                job_analysis=job_analysis,
                user_profile=user,
                resumes=[resume]
            )

            # Deterministic Primary Stack Barrier must cap score <= 35 and set fit to IGNORE
            self.assertLessEqual(res.score, 35)
            self.assertEqual(res.fit, "IGNORE")
            self.assertFalse(res.recommendation)
            self.assertTrue(any("Java" in r for r in res.missing_requirements))
            self.assertTrue(any("Java" in r for r in res.risks))

if __name__ == "__main__":
    unittest.main()

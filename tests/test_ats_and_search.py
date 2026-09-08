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

if __name__ == "__main__":
    unittest.main()

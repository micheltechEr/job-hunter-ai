import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from app.schemas.candidate_search_profile import (
    CandidateSearchProfile,
    ProfileIdentity,
    ProfileTarget,
    ProfileSkills,
    ProfileExperience,
    ProfileDomains,
    ProfilePreferences,
    SearchVocabulary
)
from app.schemas.linkedin_publication import LinkedInPublication, MatchBreakdown
from app.services.search_vocabulary import SearchVocabularyBuilder, normalize_role_title, normalize_term_with_aliases
from app.services.candidate_profile_builder import CandidateProfileBuilder
from app.services.query_generator import QueryGenerator, GeneratedQuery
from app.services.publication_normalizer import publication_normalizer
from app.services.publication_matcher import publication_matcher, QueryPerformanceTracker
from app.services.linkedin_search_provider import LinkedInSearchProvider


class TestCandidateSearchProfileAndLinkedInPipeline(unittest.TestCase):

    def setUp(self):
        # Build mock user profile mimicking real candidate
        self.mock_user_profile = MagicMock()
        self.mock_user_profile.desired_roles = ["Desenvolvedor Full Stack Jr", "Desenvolvedor Backend"]
        self.mock_user_profile.technologies = ["React", "TypeScript", "Node.js", "PHP", "Laravel"]
        self.mock_user_profile.databases = ["MySQL", "PostgreSQL", "Redis"]
        self.mock_user_profile.devops_tools = ["Git", "Docker"]
        self.mock_user_profile.cloud_providers = []
        self.mock_user_profile.seniority_level = "Junior"
        self.mock_user_profile.work_mode = "Remote"
        self.mock_user_profile.location = "Bahia, Brasil"

        mock_exp1 = MagicMock()
        mock_exp1.role = "Desenvolvedor Web"
        mock_exp1.company = "Agência Digital"
        mock_exp1.description = "Desenvolvimento de e-commerce e landing pages de alta conversão em PHP e React."
        mock_exp1.skills_used = ["PHP", "Laravel", "React", "MySQL"]
        mock_exp1.duration_months = 18

        mock_exp2 = MagicMock()
        mock_exp2.role = "Desenvolvedor Full Stack"
        mock_exp2.company = "Tech Soluções"
        mock_exp2.description = "Construção de APIs REST em Node.js e TypeScript com PostgreSQL."
        mock_exp2.skills_used = ["Node.js", "TypeScript", "PostgreSQL"]
        mock_exp2.duration_months = 14

        self.mock_user_profile.experiences = [mock_exp1, mock_exp2]

    # =========================================================================
    # 1. Profile Extraction, Normalization & UNKNOWN Standards
    # =========================================================================

    def test_candidate_profile_builder_separates_facts_and_aspirations(self):
        profile = CandidateProfileBuilder.build_from_user_profile(self.mock_user_profile)

        self.assertEqual(profile.profile_version, "1.0")
        
        # Observed roles extracted from past experiences
        self.assertIn("Desenvolvedor Web", profile.identity.roles_observed)
        self.assertIn("Desenvolvedor Full Stack", profile.identity.roles_observed)

        # Target roles preserved from desired_roles
        self.assertIn("Desenvolvedor Full Stack", profile.target.roles)
        self.assertIn("Desenvolvedor Backend", profile.target.roles)

        # Categorized skills
        self.assertIn("TypeScript", profile.skills.languages)
        self.assertIn("PHP", profile.skills.languages)
        self.assertIn("React", profile.skills.frontend)
        self.assertIn("Laravel", profile.skills.frameworks)
        self.assertIn("MySQL", profile.skills.databases)

        # Contextual domain preserved from experience descriptions
        self.assertIn("E-commerce", profile.domains.experienced)

        # UNKNOWN fallback for absent values
        empty_profile_obj = MagicMock()
        empty_profile_obj.desired_roles = []
        empty_profile_obj.technologies = []
        empty_profile_obj.databases = []
        empty_profile_obj.devops_tools = []
        empty_profile_obj.cloud_providers = []
        empty_profile_obj.seniority_level = None
        empty_profile_obj.work_mode = None
        empty_profile_obj.location = None
        empty_profile_obj.experiences = []

        empty_built = CandidateProfileBuilder.build_from_user_profile(empty_profile_obj)
        self.assertEqual(empty_built.identity.professional_area, "Desenvolvimento de Software")
        self.assertEqual(empty_built.identity.roles_observed, [])

    def test_term_normalization_and_aliases(self):
        norm_node = normalize_term_with_aliases("NodeJS")
        self.assertEqual(norm_node["canonical"], "Node.js")
        self.assertIn("Node", norm_node["aliases"])
        self.assertIn("NodeJS", norm_node["aliases"])

        norm_react = normalize_term_with_aliases("ReactJS")
        self.assertEqual(norm_react["canonical"], "React")

        norm_role = normalize_role_title("Fullstack Developer")
        self.assertEqual(norm_role["canonical"], "Desenvolvedor Full Stack")
        self.assertIn("Full Stack Developer", norm_role["aliases"])

    # =========================================================================
    # 2. Search Vocabulary & Exclusions
    # =========================================================================

    def test_search_vocabulary_derives_synonyms_and_evidence_exclusions(self):
        vocab = SearchVocabularyBuilder.build(
            roles=["Desenvolvedor Full Stack", "Desenvolvedor Backend"],
            technologies=["PHP", "React", "Node.js"],
            domains=["E-commerce"],
            seniority="Junior"
        )

        self.assertIn("Desenvolvedor Full Stack", vocab.roles)
        self.assertIn("Full Stack Developer", vocab.roles)
        self.assertIn("desenvolvedor", vocab.synonyms)
        self.assertIn("developer", vocab.synonyms["desenvolvedor"])

        # Junior seniority evidence derives senior exclusions
        self.assertIn("Senior", vocab.exclude)
        self.assertIn("Tech Lead", vocab.exclude)

    # =========================================================================
    # 3. Query Generator (Strategies, 4 Layers, Metadata, Limits)
    # =========================================================================

    def test_query_generator_creates_progressive_layers_with_metadata(self):
        profile = CandidateProfileBuilder.build_from_user_profile(self.mock_user_profile)
        gen = QueryGenerator(max_queries=12)
        queries = gen.generate_queries(profile)

        self.assertGreater(len(queries), 0)
        self.assertLessEqual(len(queries), 12)

        # Check types present
        types = [q.type for q in queries]
        self.assertIn("role_skill_combination", types)
        
        # Check Layer 1 query
        l1_queries = [q for q in queries if q.layer == 1]
        self.assertTrue(len(l1_queries) > 0)
        self.assertEqual(l1_queries[0].priority, "high")
        self.assertEqual(l1_queries[0].expected_intent, "job_posting")
        self.assertIn("role", l1_queries[0].sources)
        self.assertIn("skills", l1_queries[0].sources)

        # Ensure no duplicates
        query_texts = [q.query.lower() for q in queries]
        self.assertEqual(len(query_texts), len(set(query_texts)))

    # =========================================================================
    # 4. Publication Normalizer
    # =========================================================================

    def test_publication_normalizer_handles_missing_fields_and_extracts_entities(self):
        raw_card = {
            "title": "Desenvolvedor Full Stack Jr - React / Node",
            "company": "Startup X",
            "location": "Remoto - Brasil",
            "description": "Buscamos dev Júnior para atuar com React, Node.js, TypeScript e PostgreSQL. Modelo 100% home office CLT."
        }

        pub = publication_normalizer.normalize_publication(raw_card, query_origin='"Desenvolvedor Full Stack" React')
        self.assertEqual(pub.role, "Desenvolvedor Full Stack")
        self.assertEqual(pub.work_model, "Remote")
        self.assertEqual(pub.employment_type, "CLT")
        self.assertEqual(pub.seniority, "Junior")
        self.assertIn("React", pub.technologies)
        self.assertIn("TypeScript", pub.technologies)
        self.assertEqual(pub.query_origin, '"Desenvolvedor Full Stack" React')

        # Test UNKNOWN on empty data
        empty_pub = publication_normalizer.normalize_publication({})
        self.assertEqual(empty_pub.title, "UNKNOWN")
        self.assertEqual(empty_pub.company, "UNKNOWN")
        self.assertEqual(empty_pub.work_model, "UNKNOWN")
        self.assertEqual(empty_pub.seniority, "UNKNOWN")

    # =========================================================================
    # 5. Hybrid Matcher & Explainability
    # =========================================================================

    def test_publication_matcher_evaluates_compatible_job(self):
        async def _run():
            profile = CandidateProfileBuilder.build_from_user_profile(self.mock_user_profile)

            matching_pub = LinkedInPublication(
                source="linkedin",
                title="Desenvolvedor Full Stack Júnior",
                description="Vaga para atuar com React, Node.js, TypeScript e PostgreSQL em projetos de E-commerce. Remoto.",
                company="Loja Virtual SA",
                location="Brasil",
                work_model="Remote",
                employment_type="CLT",
                technologies=["React", "Node.js", "TypeScript", "PostgreSQL"],
                role="Desenvolvedor Full Stack",
                seniority="Junior"
            )

            res = await publication_matcher.evaluate_publication(profile, matching_pub)
            
            self.assertEqual(res.breakdown.role_match, "strong")
            self.assertEqual(res.breakdown.skill_match, "strong")
            self.assertEqual(res.breakdown.seniority_match, "strong")
            self.assertEqual(res.breakdown.work_model_match, "strong")
            self.assertTrue(res.breakdown.is_relevant)
            self.assertGreaterEqual(res.breakdown.composite_score, 70)
            self.assertIn("React", res.matched_skills)
            self.assertIn("E-commerce", res.matched_domains)

        asyncio.run(_run())

    def test_publication_matcher_disqualifies_incompatible_primary_stack(self):
        async def _run():
            profile = CandidateProfileBuilder.build_from_user_profile(self.mock_user_profile)

            # Java job (Candidate has 0 Java)
            java_pub = LinkedInPublication(
                source="linkedin",
                title="Desenvolvedor Java Backend Jr",
                description="Vaga de Java 8 / 17, Spring Boot, Hibernate e Oracle. Remoto.",
                company="Confitec",
                location="Remoto",
                work_model="Remote",
                technologies=["Java (JVM)", "Spring Boot"],
                role="Desenvolvedor Backend",
                seniority="Junior"
            )

            res = await publication_matcher.evaluate_publication(profile, java_pub)
            
            # Hard stack barrier must disqualify
            self.assertLessEqual(res.breakdown.composite_score, 30)
            self.assertFalse(res.breakdown.is_relevant)
            self.assertEqual(res.breakdown.relevance_tier, "NOT_RELEVANT")
            self.assertTrue(any("Java" in req for req in res.unmatched_requirements))
            self.assertIn("Incompatibilidade crítica", res.explanation)

        asyncio.run(_run())

    # =========================================================================
    # 6. Query Performance Tracker
    # =========================================================================

    def test_query_performance_tracker_records_metrics(self):
        tracker = QueryPerformanceTracker()
        tracker.record('"Desenvolvedor Full Stack" PHP', results_found=10, relevant_results=6)
        tracker.record('"Desenvolvedor Full Stack" PHP', results_found=5, relevant_results=3)

        stats = tracker.get_stats()
        self.assertIn('"Desenvolvedor Full Stack" PHP', stats)
        self.assertEqual(stats['"Desenvolvedor Full Stack" PHP']["results_found"], 15)
        self.assertEqual(stats['"Desenvolvedor Full Stack" PHP']["relevant_results"], 9)
        self.assertEqual(stats['"Desenvolvedor Full Stack" PHP']["relevance_rate_pct"], 60.0)

    # =========================================================================
    # 7. Search Provider End-to-End Execution
    # =========================================================================

    def test_linkedin_search_provider_pipeline(self):
        async def _run():
            provider = LinkedInSearchProvider()
            profile = CandidateProfileBuilder.build_from_user_profile(self.mock_user_profile)

            mock_scraped_jobs = [
                {
                    "title": "Desenvolvedor Full Stack Jr",
                    "company": "Empresa Alfa",
                    "url": "https://linkedin.com/jobs/view/101",
                    "description": "React, Node.js e TypeScript. Remoto."
                }
            ]

            with patch("app.services.scraper_service.scraper_service.scrape_linkedin_jobs", new_callable=AsyncMock) as mock_scrape, \
                 patch("app.services.scraper_service.scraper_service.scrape_linkedin_posts", new_callable=AsyncMock) as mock_scrape_posts:
                mock_scrape.return_value = mock_scraped_jobs
                mock_scrape_posts.return_value = []

                results = await provider.search_and_match_pipeline(
                    candidate_profile=profile,
                    max_queries=2,
                    results_per_query=2
                )

                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].publication.title, "Desenvolvedor Full Stack Jr")
                self.assertEqual(results[0].publication.company, "Empresa Alfa")
                self.assertTrue(results[0].breakdown.is_relevant)

        asyncio.run(_run())

    def test_candidate_profile_json_schema_contract(self):
        profile = CandidateProfileBuilder.build_from_user_profile(self.mock_user_profile)
        json_str = profile.to_canonical_json()
        self.assertIn('"profile_version": "1.0"', json_str)
        self.assertIn('"identity"', json_str)
        self.assertIn('"roles_observed"', json_str)
        self.assertIn('"target"', json_str)
        self.assertIn('"skills"', json_str)
        self.assertIn('"search_vocabulary"', json_str)


if __name__ == "__main__":
    unittest.main()

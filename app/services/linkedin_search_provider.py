import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
from app.schemas.candidate_search_profile import CandidateSearchProfile
from app.schemas.linkedin_publication import LinkedInPublication, PublicationMatchResult
from app.services.query_generator import GeneratedQuery, QueryGenerator
from app.services.publication_normalizer import publication_normalizer
from app.services.publication_matcher import publication_matcher
from app.services.scraper_service import scraper_service

logger = logging.getLogger("job_hunter.linkedin_search_provider")


class BaseSearchProvider(ABC):
    """Abstract search provider interface."""
    @abstractmethod
    async def search(
        self,
        query: GeneratedQuery,
        location: str = "Brasil",
        limit: int = 5
    ) -> List[LinkedInPublication]:
        pass


class LinkedInSearchProvider(BaseSearchProvider):
    """
    LinkedIn Publication and Job Search Provider.
    Integrates directly with the existing Playwright engine without duplicating scraper infrastructure.
    """

    def __init__(self):
        self.query_generator = QueryGenerator()

    async def search(
        self,
        query: GeneratedQuery,
        location: str = "Brasil",
        limit: int = 5
    ) -> List[LinkedInPublication]:
        raw_query = query.query
        logger.info(f"Executing LinkedIn search query [{query.type}|L{query.layer}|{query.priority}]: '{raw_query}' (limit={limit})")

        # Reuse existing scraper infrastructure via scraper_service
        try:
            raw_jobs = await scraper_service.scrape_linkedin_jobs(
                keyword=raw_query,
                location=location,
                limit=limit,
                exclude_senior=False # We let publication_matcher perform fine-grained gating
            )
        except Exception as e:
            logger.error(f"Error executing LinkedIn scraper for query '{raw_query}': {e}")
            raw_jobs = []

        normalized_publications: List[LinkedInPublication] = []
        for r_job in raw_jobs:
            pub = publication_normalizer.normalize_publication(
                raw_data=r_job,
                query_origin=raw_query
            )
            normalized_publications.append(pub)

        return normalized_publications

    async def search_and_match_pipeline(
        self,
        candidate_profile: CandidateSearchProfile,
        location: str = "Brasil",
        max_queries: int = 6,
        results_per_query: int = 5
    ) -> List[PublicationMatchResult]:
        """
        Complete end-to-end pipeline:
        Profile -> Queries -> Search -> Normalization -> Hybrid Matching -> Explainability
        """
        queries = self.query_generator.generate_queries(candidate_profile, max_queries=max_queries)
        logger.info(f"Generated {len(queries)} prioritized queries for candidate search profile.")

        all_results: List[PublicationMatchResult] = []
        seen_urls: set = set()

        for q in queries:
            pubs = await self.search(q, location=location, limit=results_per_query)
            query_results_found = len(pubs)
            query_relevant_found = 0

            for pub in pubs:
                if pub.url != "UNKNOWN" and pub.url in seen_urls:
                    continue
                if pub.url != "UNKNOWN":
                    seen_urls.add(pub.url)

                match_result = await publication_matcher.evaluate_publication(candidate_profile, pub)
                if match_result.breakdown.is_relevant:
                    query_relevant_found += 1
                all_results.append(match_result)

            # Record performance for this query
            publication_matcher.performance_tracker.record(
                query=q.query,
                results_found=query_results_found,
                relevant_results=query_relevant_found
            )

        # Sort all matched publications by composite score descending
        all_results.sort(key=lambda r: r.breakdown.composite_score, reverse=True)
        return all_results


linkedin_search_provider = LinkedInSearchProvider()

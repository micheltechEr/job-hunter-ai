import asyncio
import logging
from typing import Optional, Set, List
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.db_models import Job, JobAnalysis, Application, UserProfile
from app.services.job_analysis import job_analysis_service
from app.services.matching import matching_service
from app.services.application_generator import application_generator_service

logger = logging.getLogger("job_hunter.ats_queue")


class ATSWorkerQueue:
    """
    Asynchronous queue and worker pool for processing Job Analysis and ATS Matching
    in the background without blocking search or user operations, with concurrency limits.
    """

    def __init__(self, max_concurrency: int = 2):
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.processing_job_ids: Set[int] = set()
        self.max_concurrency = max_concurrency
        self.workers: List[asyncio.Task] = []
        self._running = False
        self._lock = asyncio.Lock()

    async def ensure_running(self):
        """Ensures that background workers are active and healthy."""
        async with self._lock:
            # Clean up dead worker tasks if any
            self.workers = [t for t in self.workers if not t.done()]
            if not self._running or len(self.workers) < self.max_concurrency:
                self._running = True
                needed = self.max_concurrency - len(self.workers)
                for _ in range(needed):
                    idx = len(self.workers) + 1
                    task = asyncio.create_task(self._worker(f"ats-worker-{idx}"))
                    self.workers.append(task)
                logger.info(f"ATS Worker Queue active with {len(self.workers)} workers.")

    async def start(self):
        """Starts worker tasks and recovers unanalyzed jobs from DB."""
        await self.ensure_running()
        asyncio.create_task(self._recover_pending_jobs())

    async def shutdown(self):
        """Stops workers gracefully."""
        self._running = False
        for task in self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        logger.info("ATS Worker Queue shut down.")

    async def enqueue(self, job_id: int):
        """Enqueues a job ID for background ATS processing, ensuring workers are running."""
        await self.ensure_running()
        async with self._lock:
            if job_id in self.processing_job_ids:
                return
            self.processing_job_ids.add(job_id)
            await self.queue.put(job_id)
            logger.info(f"Enqueued job {job_id} for background ATS processing (Queue size: {self.queue.qsize()})")

    async def _recover_pending_jobs(self):
        """Checks DB on startup and enqueues jobs that have missing analysis or pending ATS."""
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Job)
                    .options(selectinload(Job.analysis), selectinload(Job.application))
                )
                jobs = result.scalars().all()
                recovered = 0
                for job in jobs:
                    needs_ats = (
                        not job.analysis
                        or not job.application
                        or job.application.score is None
                        or job.application.status == "ANALYZING"
                    )
                    if needs_ats:
                        await self.enqueue(job.id)
                        recovered += 1
                if recovered > 0:
                    logger.info(f"Recovered and enqueued {recovered} pending/unscored jobs for ATS.")
        except Exception as e:
            logger.warning(f"Could not auto-recover pending jobs on startup: {e}")

    async def _worker(self, worker_name: str):
        """Worker loop picking jobs from queue and executing ATS pipeline."""
        while self._running:
            try:
                job_id = await self.queue.get()
            except asyncio.CancelledError:
                break
            except Exception as get_err:
                logger.error(f"[{worker_name}] Queue get error: {get_err}")
                await asyncio.sleep(1)
                continue

            try:
                logger.info(f"[{worker_name}] Processing ATS for job {job_id}...")
                await self._process_job_ats(job_id)
            except Exception as e:
                logger.error(f"[{worker_name}] Error processing ATS for job {job_id}: {e}", exc_info=True)
            finally:
                async with self._lock:
                    self.processing_job_ids.discard(job_id)
                self.queue.task_done()

    async def _process_job_ats(self, job_id: int):
        """Executes Job Analysis -> ATS Matching -> Application Draft in one robust pipeline."""
        async with AsyncSessionLocal() as db:
            try:
                # 1. Fetch Job
                res = await db.execute(
                    select(Job)
                    .options(selectinload(Job.analysis), selectinload(Job.application))
                    .where(Job.id == job_id)
                )
                job = res.scalars().first()
                if not job:
                    return

                # 2. Extract Job Analysis if missing
                if not job.analysis:
                    try:
                        analysis_data = await job_analysis_service.analyze_job_description(job.description)
                        db_analysis = JobAnalysis(
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
                        db.add(db_analysis)
                        await db.commit()
                        await db.refresh(job)
                    except Exception as an_err:
                        logger.warning(f"Error during job {job_id} analysis: {an_err}")

                # 3. Calculate ATS Match
                match_data = None
                try:
                    match_data = await matching_service.match_job_profile(db=db, job_id=job.id)
                except Exception as match_err:
                    logger.warning(f"Skipping ATS match for job {job_id}: {match_err}")

                # 4. Check or create application record
                res_app = await db.execute(select(Application).where(Application.job_id == job.id))
                app = res_app.scalars().first()
                if not app:
                    app = Application(job_id=job.id, status="DISCOVERED")
                    db.add(app)

                if match_data:
                    app.score = match_data.score
                    app.fit = match_data.fit
                    app.resume_id = match_data.recommended_resume_id
                    app.status = "DISCOVERED" if match_data.score < 80 else "HIGH_MATCH"

                    # 5. If high match, generate personalized application email draft
                    if match_data.score >= 80:
                        try:
                            res_prof = await db.execute(
                                select(UserProfile)
                                .options(selectinload(UserProfile.experiences), selectinload(UserProfile.projects))
                                .limit(1)
                            )
                            prof = res_prof.scalars().first()
                            if prof and job.analysis:
                                draft = await application_generator_service.generate_draft(prof, job, job.analysis)
                                app.email_subject = draft.subject
                                app.email_body = draft.body
                                if draft.recipient_email:
                                    app.recipient_email = draft.recipient_email
                        except Exception as draft_err:
                            logger.warning(f"Could not generate auto-draft for job {job.id}: {draft_err}")

                await db.commit()
                logger.info(f"Finished ATS processing for job {job_id}: {job.title} (Score: {getattr(app, 'score', 'N/A')})")

            except Exception as e:
                logger.error(f"Failed ATS pipeline for job {job_id}: {e}")
                await db.rollback()


ats_worker_queue = ATSWorkerQueue(max_concurrency=2)

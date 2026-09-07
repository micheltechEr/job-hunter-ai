import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.future import select
from app.database import AsyncSessionLocal
from app.models.db_models import UserProfile, Job, Application
from app.services.scraper_service import scraper_service
from app.api.jobs import generate_application_endpoint

logger = logging.getLogger("job_hunter.scheduler")

scheduler = AsyncIOScheduler()

async def run_job_hunting_scrape():
    """Periodic job that queries LinkedIn/scrapers using desired_roles configurations and triggers auto-matching."""
    logger.info("Starting automated job hunting scrape check...")
    
    async with AsyncSessionLocal() as db:
        try:
            # 1. Fetch UserProfile to retrieve keywords
            result = await db.execute(select(UserProfile).limit(1))
            profile = result.scalars().first()
            
            if not profile or not profile.desired_roles:
                logger.warning("No User Profile or desired_roles registered. Skipping automated scraping.")
                return
            
            desired_roles = profile.desired_roles
            location = profile.location or "Brasil"
            
            logger.info(f"Targeting search profiles cargos: {desired_roles} at location: {location}")
            
            # 2. Iterate each cargo and scrape
            for role in desired_roles:
                logger.info(f"Checking updates for role: {role}")
                
                # LinkedIn
                li_list = await scraper_service.scrape_linkedin_jobs(keyword=role, location=location, limit=2)
                if li_list:
                    await scraper_service.ingest_new_jobs(db, li_list)
                
                # Programathor
                pt_list = await scraper_service.scrape_programathor_jobs(keyword=role, limit=2)
                if pt_list:
                    await scraper_service.ingest_new_jobs(db, pt_list)
                
                # Gupy Portal
                gp_list = await scraper_service.scrape_gupy_jobs(keyword=role, limit=2)
                if gp_list:
                    await scraper_service.ingest_new_jobs(db, gp_list)
                
                # 3. Auto-draft application candidate reviews for high-scoring jobs
                result_jobs = await db.execute(select(Job))
                inserted_jobs = result_jobs.scalars().all()
                
                for job in inserted_jobs:
                    # Check if application exists
                    res_app = await db.execute(select(Application).where(Application.job_id == job.id))
                    app = res_app.scalars().first()
                    
                    if not app:
                        try:
                            # Trigger draft generation automatically mapping the application endpoint
                            await generate_application_endpoint(job.id, db)
                            logger.info(f"Automatically generated application draft for job: {job.title}")
                        except Exception as app_ex:
                            logger.warning(f"Skipping auto-app-draft generation for job {job.id}: {app_ex}")
                            continue
            
            logger.info("Automated job hunting scrape run completed successfully.")
            
        except Exception as e:
            logger.error(f"Error executing scraper scheduler job: {e}")
            await db.rollback()

def start_scheduler():
    """Boots the periodic scheduler triggers."""
    if not scheduler.running:
        # Run every 6 hours
        scheduler.add_job(
            run_job_hunting_scrape,
            "interval",
            hours=6,
            id="job_hunter_scrape_task",
            replace_existing=True
        )
        scheduler.start()
        logger.info("APScheduler initialized and running periodically (every 6 hours).")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("APScheduler stopped.")

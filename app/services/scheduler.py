import logging
from typing import List, Optional, Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.future import select
from app.database import AsyncSessionLocal
from app.models.db_models import UserProfile, Job, Application
from app.services.scraper_service import scraper_service, resolve_exclude_senior

logger = logging.getLogger("job_hunter.scheduler")

scheduler = AsyncIOScheduler()

async def run_job_hunting_scrape(
    roles: Optional[List[str]] = None,
    location: Optional[str] = None,
    seniority: Optional[str] = None,
    platforms: Optional[List[str]] = None,
    limit_per_platform: int = 5,
    exclude_senior: bool = True,
    save_to_profile: bool = False
) -> Dict[str, Any]:
    """Scrapes jobs from LinkedIn, Programathor, and Gupy based on requested roles or profile desired_roles."""
    logger.info("Starting automated job hunting scrape check...")
    
    async with AsyncSessionLocal() as db:
        try:
            # 1. Fetch UserProfile to retrieve defaults if needed
            result = await db.execute(select(UserProfile).limit(1))
            profile = result.scalars().first()
            
            # Determine target roles
            target_roles: List[str] = []
            if roles and len(roles) > 0:
                target_roles = [r.strip() for r in roles if r and r.strip()]
            
            if not target_roles:
                if profile and profile.desired_roles:
                    target_roles = [r.strip() for r in profile.desired_roles if r and r.strip()]
            
            if not target_roles:
                target_roles = ["Desenvolvedor Python", "Engenheiro de Software"]
                logger.info(f"No specific roles provided or in profile. Using fallback: {target_roles}")
            
            # Determine location
            target_location = location.strip() if location and location.strip() else (profile.location if profile and profile.location else "Brasil")
            
            # Determine candidate seniority and senior exclusion rule
            profile_sen = profile.seniority_level if profile else "Junior"
            should_exclude_senior = resolve_exclude_senior(
                seniority=seniority,
                profile_seniority=profile_sen,
                explicit_exclude=exclude_senior
            )
            
            # Determine platforms
            valid_platforms = {"linkedin", "programathor", "gupy"}
            if platforms:
                chosen_platforms = [p.strip().lower() for p in platforms if p and p.strip().lower() in valid_platforms]
            else:
                chosen_platforms = ["linkedin", "programathor", "gupy"]
            if not chosen_platforms:
                chosen_platforms = ["linkedin", "programathor", "gupy"]
            
            # Persist roles to profile if requested
            if save_to_profile and target_roles:
                if profile:
                    existing_roles = list(profile.desired_roles or [])
                    merged = list(dict.fromkeys(existing_roles + target_roles))
                    profile.desired_roles = merged
                    if location and location.strip():
                        profile.location = target_location
                    if seniority and seniority.strip():
                        profile.seniority_level = seniority.strip()
                    await db.commit()
                    await db.refresh(profile)
                    logger.info(f"Updated user profile desired_roles to: {profile.desired_roles} (Seniority: {profile.seniority_level})")
                else:
                    new_profile = UserProfile(
                        name="Candidato",
                        desired_roles=target_roles,
                        location=target_location,
                        seniority_level=seniority or "Junior"
                    )
                    db.add(new_profile)
                    await db.commit()
                    logger.info(f"Created new user profile with desired_roles: {target_roles}")
            
            logger.info(f"Targeting search roles: {target_roles} at location: {target_location} (exclude_senior={should_exclude_senior}) on platforms: {chosen_platforms} (limit: {limit_per_platform})")
            
            total_ingested = 0
            
            # 2. Iterate each role and scrape from selected platforms
            for role in target_roles:
                logger.info(f"Scraping active jobs for target role: '{role}' (exclude_senior={should_exclude_senior})")
                
                # LinkedIn
                if "linkedin" in chosen_platforms:
                    try:
                        li_list = await scraper_service.scrape_linkedin_jobs(keyword=role, location=target_location, limit=limit_per_platform, exclude_senior=should_exclude_senior)
                        if li_list:
                            await scraper_service.ingest_new_jobs(db, li_list, exclude_senior=should_exclude_senior)
                            total_ingested += len(li_list)
                    except Exception as li_err:
                        logger.error(f"Error scraping LinkedIn for '{role}': {li_err}")
                
                # Programathor
                if "programathor" in chosen_platforms:
                    try:
                        pt_list = await scraper_service.scrape_programathor_jobs(keyword=role, limit=limit_per_platform, exclude_senior=should_exclude_senior)
                        if pt_list:
                            await scraper_service.ingest_new_jobs(db, pt_list, exclude_senior=should_exclude_senior)
                            total_ingested += len(pt_list)
                    except Exception as pt_err:
                        logger.error(f"Error scraping Programathor for '{role}': {pt_err}")
                
                # Gupy Portal
                if "gupy" in chosen_platforms:
                    try:
                        gp_list = await scraper_service.scrape_gupy_jobs(keyword=role, limit=limit_per_platform, exclude_senior=should_exclude_senior)
                        if gp_list:
                            await scraper_service.ingest_new_jobs(db, gp_list, exclude_senior=should_exclude_senior)
                            total_ingested += len(gp_list)
                    except Exception as gp_err:
                        logger.error(f"Error scraping Gupy for '{role}': {gp_err}")
            
            logger.info(f"Job hunting scrape run completed. Scraped {total_ingested} job listings enqueued for background ATS.")
            return {
                "roles": target_roles,
                "location": target_location,
                "seniority": profile_sen,
                "exclude_senior": should_exclude_senior,
                "platforms": chosen_platforms,
                "total_ingested": total_ingested
            }
            
        except Exception as e:
            logger.error(f"Error executing scraper scheduler job: {e}")
            await db.rollback()
            return {"error": str(e)}

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

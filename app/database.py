import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models.db_models import Base

logger = logging.getLogger("job_hunter.database")

# Handle sqlite async details if using sqlite
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

try:
    engine = create_async_engine(
        settings.DATABASE_URL,
        connect_args=connect_args,
        echo=False
    )
    AsyncSessionLocal = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
except Exception as e:
    logger.error(f"Error creating async engine with URL: {settings.DATABASE_URL}. Details: {e}")
    raise e

async def get_db():
    """Dependency helper to get async session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db():
    """Initializes the database. Creates schemas and tables."""
    async with engine.begin() as conn:
        try:
            # Recreate tables if they don't exist
            await conn.run_sync(Base.metadata.create_all)
            
            # Safe idempotent column additions
            from sqlalchemy import text
            alter_queries = [
                "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS seniority_level VARCHAR(50);",
                "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS years_of_experience FLOAT;"
            ]
            for q in alter_queries:
                try:
                    await conn.execute(text(q))
                except Exception as alter_err:
                    logger.debug(f"Column migration notice: {alter_err}")
                    
            logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise e

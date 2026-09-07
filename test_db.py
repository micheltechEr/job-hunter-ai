import asyncio
import sys
import os

# Append project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import init_db, engine
from app.config import settings

async def main():
    print(f"Testing DB Init with Database URL: {settings.DATABASE_URL}")
    try:
        await init_db()
        print("Success! Database initialized successfully.")
    except Exception as e:
        print(f"Database initialization failed: {e}")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())

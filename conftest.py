import asyncio
import sys
import pytest

# Ensure Windows uses ProactorEventLoopPolicy for Playwright compatibility
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

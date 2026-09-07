import sys
import os
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi.testclient import TestClient

# Append project root
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Override database URL to sqlite in memory for testing
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from app.main import app

def test_verify_application_routes():
    with TestClient(app) as client:
        # Check index page (Dashboard)
        print("Testing GET / ...")
        response = client.get("/")
        assert response.status_code == 200, "Dashboard root should load successfully with 200"
        print("GET / passed.")

        # Check resumes API list
        print("Testing GET /api/resumes/ ...")
        response = client.get("/api/resumes/")
        assert response.status_code == 200, "Resumes list endpoint should return 200"
        assert isinstance(response.json(), list), "Resumes list should return a list"
        print("GET /api/resumes/ passed.")

        # Check jobs API list
        print("Testing GET /api/jobs/ ...")
        response = client.get("/api/jobs/")
        assert response.status_code == 200, "Jobs list endpoint should return 200"
        assert isinstance(response.json(), list), "Jobs list should return a list"
        print("GET /api/jobs/ passed.")

        # Check Gmail API status
        print("Testing GET /api/gmail/status ...")
        response = client.get("/api/gmail/status")
        assert response.status_code == 200, "Gmail status endpoint should return 200"
        data = response.json()
        assert "authenticated" in data, "Gmail status should contain 'authenticated' field"
        print("GET /api/gmail/status passed.")

        # Check Scrape Trigger API status
        print("Testing POST /api/jobs/scrape/trigger ...")
        response = client.post("/api/jobs/scrape/trigger")
        assert response.status_code == 202, "Scrape trigger endpoint should return 202"
        data = response.json()
        assert "message" in data, "Scrape response should contain message"
        print("POST /api/jobs/scrape/trigger passed.")

    print("\n--- ALL STATIC CHECKS PASSED SUCCESSFULLY ---")

if __name__ == "__main__":
    try:
        test_verify_application_routes()
    except AssertionError as ae:
        print(f"VERIFICATION FAILED: {ae}")
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"CRITICAL ERROR RUNNING VERIFICATION: {e}")
        sys.exit(1)

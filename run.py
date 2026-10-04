"""Single-command startup: python run.py."""
import uvicorn
from backend.config import settings

if __name__ == "__main__":
    print(f"Competitor Intelligence: http://{settings.app_host}:{settings.app_port}")
    uvicorn.run("backend.main:app", host=settings.app_host, port=settings.app_port,
                reload=settings.app_env == "development", log_level=settings.log_level.lower(),
                access_log=False, proxy_headers=False)

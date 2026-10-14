"""Local Web entry point. Importing the API never starts a browser or loads a model."""
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from sources.web_api import create_app

api = create_app(ROOT)

if __name__ == "__main__":
    # Tools execute code: listen on loopback unless explicitly deployed.
    host = os.getenv("BACKEND_HOST", "0.0.0.0" if Path("/.dockerenv").exists() else "127.0.0.1")
    uvicorn.run(api, host=host, port=int(os.getenv("BACKEND_PORT") or "7777"))

"""
Main entry point for the application.
"""

import os
import subprocess
from typing import Any
from dotenv import load_dotenv

load_dotenv()


def required_env(name: str) -> Any:
    """Get required environment variable or raise error."""
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"Environment variable '{name}' is required but not set.")
    return value


port = int(required_env("PORT"))
dev = required_env("DEV")

try:
    print(port, dev, os.getcwd())
    subprocess.run(
        [
            "uvicorn",
            "src.server:app",
            *(["--reload"] if dev else []),
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ],
        cwd=os.getcwd(),
        check=True,
    )
except KeyboardInterrupt:
    print("Server stopped by user.")
except Exception as e:
    print(f"An error occurred while starting the server: {e}")
    raise

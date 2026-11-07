"""
Main entry point for the application.
"""

import uvicorn

from src.config import DEV, HOST, PORT


if __name__ == "__main__":
    uvicorn.run("src.server:app", host=HOST, port=PORT, reload=DEV)

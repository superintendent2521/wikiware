from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List
import markdown
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from database import init_database, get_pages_collection, get_history_collection, db_instance
from loguru import logger

load_dotenv()

# Configure loguru
os.makedirs("logs", exist_ok=True)
logger.add("logs/wikiware.log", rotation="1 day", retention="7 days", level="INFO")
logger.add("logs/errors.log", rotation="1 day", retention="7 days", level="ERROR")

def is_valid_title(title: str) -> bool:
    """
    Validate title to prevent path traversal or other issues.
    
    Args:
        title: The title to validate
        
    Returns:
        bool: True if title is valid, False otherwise
    """
    return title and ".." not in title and not title.startswith("/")

app = FastAPI(title="WikiWare", description="A simple wiki software")

# Templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

class WikiPage(BaseModel):
    title: str
    content: str
    author: Optional[str] = "Anonymous"
    created_at: datetime = datetime.now(timezone.utc)
    updated_at: datetime = datetime.now(timezone.utc)

# Startup event
@app.on_event("startup")
async def startup_event():
    try:
        await init_database()
        logger.info("WikiWare application started successfully")
    except Exception as e:
        logger.error(f"Error during application startup: {str(e)}")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not db_instance.is_connected:
        return templates.TemplateResponse("home.html", {"request": request, "pages": [], "offline": True})

    pages_collection = get_pages_collection()
    if pages_collection is not None:
        pages = await pages_collection.find().sort("updated_at", -1).to_list(100)
    else:
        pages = []

    return templates.TemplateResponse("home.html", {"request": request, "pages": pages, "offline": not db_instance.is_connected})

@app.get("/page/{title}", response_class=HTMLResponse)
async def get_page(request: Request, title: str):
    try:
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing page: {title}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

        pages_collection = get_pages_collection()
        if pages_collection is not None:
            page = await pages_collection.find_one({"title": title})
            if not page:
                logger.info(f"Page not found - viewing edit page: {title}")
                return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": False})

            page["html_content"] = markdown.markdown(page["content"])
            logger.info(f"Page viewed: {title}")
            return templates.TemplateResponse("page.html", {"request": request, "page": page, "offline": False})
        else:
            logger.error(f"Pages collection not available - viewing page: {title}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})
    except Exception as e:
        logger.error(f"Error viewing page {title}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

@app.get("/edit/{title}", response_class=HTMLResponse)
async def edit_page(request: Request, title: str):
    try:
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - editing page: {title}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

        pages_collection = get_pages_collection()
        content = ""
        if pages_collection is not None:
            page = await pages_collection.find_one({"title": title})
            content = page["content"] if page else ""

        logger.info(f"Page edit accessed: {title}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": content, "offline": not db_instance.is_connected})
    except Exception as e:
        logger.error(f"Error accessing edit page {title}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

@app.post("/edit/{title}")
async def save_page(title: str, content: str = Form(...), author: str = Form("Anonymous")):
    try:
        if not db_instance.is_connected:
            logger.error(f"Database not connected - saving page: {title}")
            return {"error": "Database not available"}

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()

        if pages_collection is None:
            logger.error(f"Pages collection not available - saving page: {title}")
            return {"error": "Database not available"}

        existing_page = await pages_collection.find_one({"title": title})

        if existing_page:
            # Save to history
            if history_collection is not None:
                await history_collection.insert_one({
                    "title": title,
                    "content": existing_page["content"],
                    "author": existing_page.get("author", "Anonymous"),
                    "updated_at": existing_page["updated_at"]
                })

            # Update page
            await pages_collection.update_one(
                {"title": title},
                {"$set": {"content": content, "author": author, "updated_at": datetime.now(timezone.utc)}}
            )
            logger.info(f"Page edited: {title} by {author}")
        else:
            # Create new page
            await pages_collection.insert_one({
                "title": title,
                "content": content,
                "author": author,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            })
            logger.info(f"Page created: {title} by {author}")

        return RedirectResponse(url=f"/page/{title}?updated=true", status_code=303)
    except Exception as e:
        logger.error(f"Error saving page {title}: {str(e)}")
        return {"error": f"Failed to save page: {str(e)}"}

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    try:
        if not db_instance.is_connected:
            logger.warning("Database not connected - search attempted")
            return templates.TemplateResponse("search.html", {"request": request, "pages": [], "query": q, "offline": True})

        pages_collection = get_pages_collection()
        pages = []

        if q and pages_collection is not None:
            pages = await pages_collection.find({"$or": [
                {"title": {"$regex": q, "$options": "i"}},
                {"content": {"$regex": q, "$options": "i"}}
            ]}).to_list(100)
            logger.info(f"Search performed: '{q}' - found {len(pages)} results")
        else:
            logger.info("Search accessed without query")

        return templates.TemplateResponse("search.html", {"request": request, "pages": pages, "query": q, "offline": not db_instance.is_connected})
    except Exception as e:
        logger.error(f"Error during search '{q}': {str(e)}")
        return templates.TemplateResponse("search.html", {"request": request, "pages": [], "query": q, "offline": True})

@app.get("/history/{title}", response_class=HTMLResponse)
async def page_history(request: Request, title: str):
    try:
        # Sanitize title to prevent path traversal or other issues
        if not is_valid_title(title):
            logger.warning(f"Invalid title for history: {title}")
            return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "error": "Invalid page title"})

        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing history: {title}")
            return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "offline": True})

        history_collection = get_history_collection()
        versions = []
        
        try:
            if history_collection is not None:
                # Get history versions
                versions = await history_collection.find({"title": title}).sort("updated_at", -1).to_list(100)
                # Get current version
                pages_collection = get_pages_collection()
                if pages_collection is not None:
                    current = await pages_collection.find_one({"title": title})
                    if current:
                        versions.insert(0, current)  # Add current version at the beginning
        except Exception as db_error:
            logger.error(f"Database error while fetching history for {title}: {str(db_error)}")
            return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "error": "Database error occurred"})

        logger.info(f"History viewed: {title}")
        return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": versions, "offline": not db_instance.is_connected})
    except Exception as e:
        logger.error(f"Error viewing history {title}: {str(e)}")
        return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "error": "An error occurred while loading history"})

@app.get("/history/{title}/{version_index}", response_class=HTMLResponse)
async def view_version(request: Request, title: str, version_index: int):
    try:
        # Sanitize title to prevent path traversal or other issues
        if not is_valid_title(title):
            logger.warning(f"Invalid title for version view: {title}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Invalid page title"})

        # Validate version index
        if version_index < 0:
            logger.warning(f"Invalid version index: {version_index} for title: {title}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Invalid version index"})

        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing version: {title} v{version_index}")
            return templates.TemplateResponse("page.html", {"request": request, "title": title, "content": "", "offline": True})

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()
        
        if pages_collection is None or history_collection is None:
            logger.error(f"Database collections not available - viewing version: {title} v{version_index}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Database not available"})

        page = None
        try:
            if version_index == 0:
                # Current version
                page = await pages_collection.find_one({"title": title})
            else:
                # Historical version
                versions = await history_collection.find({"title": title}).sort("updated_at", -1).to_list(100)
                if version_index - 1 < len(versions):
                    page = versions[version_index - 1]
        except Exception as db_error:
            logger.error(f"Database error while fetching version {version_index} for {title}: {str(db_error)}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Database error occurred"})

        if not page:
            logger.info(f"Version not found - viewing edit page: {title} v{version_index}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": False})

        try:
            page["html_content"] = markdown.markdown(page["content"])
        except Exception as md_error:
            logger.error(f"Error rendering markdown for version {version_index} of {title}: {str(md_error)}")
            page["html_content"] = page["content"]  # Fallback to raw content

        logger.info(f"Version viewed: {title} v{version_index}")
        return templates.TemplateResponse("version.html", {
            "request": request, 
            "page": page, 
            "version_num": version_index,
            "version_index": version_index,
            "offline": not db_instance.is_connected
        })
    except Exception as e:
        logger.error(f"Error viewing version {title} v{version_index}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "An error occurred while loading version"})

@app.post("/restore/{title}/{version_index}")
async def restore_version(title: str, version_index: int):
    try:
        # Sanitize title to prevent path traversal or other issues
        if not is_valid_title(title):
            logger.warning(f"Invalid title for restore: {title}")
            return RedirectResponse(url=f"/page/{title}", status_code=303)

        # Validate version index
        if version_index < 0:
            logger.warning(f"Invalid version index: {version_index} for title: {title}")
            return RedirectResponse(url=f"/page/{title}", status_code=303)

        if not db_instance.is_connected:
            logger.error(f"Database not connected - restoring version: {title} v{version_index}")
            return RedirectResponse(url=f"/page/{title}?error=database_not_available", status_code=303)

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()
        
        if pages_collection is None or history_collection is None:
            logger.error(f"Database collections not available - restoring version: {title} v{version_index}")
            return RedirectResponse(url=f"/page/{title}?error=database_not_available", status_code=303)

        page = None
        try:
            if version_index == 0:
                # Current version - nothing to restore
                logger.info(f"Attempt to restore current version (no action): {title} v{version_index}")
                return RedirectResponse(url=f"/page/{title}", status_code=303)
            else:
                # Historical version
                versions = await history_collection.find({"title": title}).sort("updated_at", -1).to_list(100)
                if version_index - 1 < len(versions):
                    page = versions[version_index - 1]
        except Exception as db_error:
            logger.error(f"Database error while fetching version {version_index} for restore {title}: {str(db_error)}")
            return RedirectResponse(url=f"/page/{title}?error=database_error", status_code=303)
        
        if not page:
            logger.error(f"Version not found for restore: {title} v{version_index}")
            return RedirectResponse(url=f"/page/{title}?error=version_not_found", status_code=303)

        try:
            # Save current version to history before restoring
            current_page = await pages_collection.find_one({"title": title})
            if current_page:
                await history_collection.insert_one({
                    "title": title,
                    "content": current_page["content"],
                    "author": current_page.get("author", "Anonymous"),
                    "updated_at": current_page["updated_at"]
                })

            # Restore the version
            await pages_collection.update_one(
                {"title": title},
                {"$set": {
                    "content": page["content"],
                    "author": page.get("author", "Anonymous"),
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
        except Exception as db_error:
            logger.error(f"Database error while restoring version {version_index} of {title}: {str(db_error)}")
            return RedirectResponse(url=f"/page/{title}?error=restore_failed", status_code=303)

        logger.info(f"Version restored: {title} v{version_index}")
        return RedirectResponse(url=f"/page/{title}?restored=true", status_code=303)
    except Exception as e:
        logger.error(f"Error restoring version {title} v{version_index}: {str(e)}")
        return RedirectResponse(url=f"/page/{title}?error=restore_error", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

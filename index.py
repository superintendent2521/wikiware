from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

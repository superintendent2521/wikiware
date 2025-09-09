from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional
import markdown
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from database import init_database, get_pages_collection, get_history_collection, db_instance

load_dotenv()

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
    await init_database()

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
    if not db_instance.is_connected:
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

    pages_collection = get_pages_collection()
    if pages_collection is not None:
        page = await pages_collection.find_one({"title": title})
        if not page:
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": False})

        page["html_content"] = markdown.markdown(page["content"])
        return templates.TemplateResponse("page.html", {"request": request, "page": page, "offline": False})
    else:
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

@app.get("/edit/{title}", response_class=HTMLResponse)
async def edit_page(request: Request, title: str):
    if not db_instance.is_connected:
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

    pages_collection = get_pages_collection()
    content = ""
    if pages_collection is not None:
        page = await pages_collection.find_one({"title": title})
        content = page["content"] if page else ""

    return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": content, "offline": not db_instance.is_connected})

@app.post("/edit/{title}")
async def save_page(title: str, content: str = Form(...), author: str = Form("Anonymous")):
    if not db_instance.is_connected:
        return {"error": "Database not available"}

    pages_collection = get_pages_collection()
    history_collection = get_history_collection()

    if pages_collection is None:
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
    else:
        # Create new page
        await pages_collection.insert_one({
            "title": title,
            "content": content,
            "author": author,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        })

    return {"message": "Page saved successfully"}

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    if not db_instance.is_connected:
        return templates.TemplateResponse("search.html", {"request": request, "pages": [], "query": q, "offline": True})

    pages_collection = get_pages_collection()
    pages = []

    if q and pages_collection is not None:
        pages = await pages_collection.find({"$or": [
            {"title": {"$regex": q, "$options": "i"}},
            {"content": {"$regex": q, "$options": "i"}}
        ]}).to_list(100)

    return templates.TemplateResponse("search.html", {"request": request, "pages": pages, "query": q, "offline": not db_instance.is_connected})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

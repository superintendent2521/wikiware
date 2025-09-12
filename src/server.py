from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List
import markdown
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from .database import init_database, get_pages_collection, get_history_collection, get_branches_collection, db_instance
from loguru import logger
import asyncio
from .stats import get_stats
import uuid
import shutil

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
    branch: Optional[str] = "main"
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
async def home(request: Request, branch: str = "main"):
    if not db_instance.is_connected:
        return templates.TemplateResponse("home.html", {"request": request, "pages": [], "offline": True, "branch": branch})

    pages_collection = get_pages_collection()
    branches_collection = get_branches_collection()
    
    # Get available branches
    branches = ["main"]
    if branches_collection is not None:
        branch_docs = await branches_collection.find().to_list(100)
        branches = list(set(["main"] + [doc["branch_name"] for doc in branch_docs]))
    
    if pages_collection is not None:
        pages = await pages_collection.find({"branch": branch}).sort("updated_at", -1).to_list(100)
    else:
        pages = []

    return templates.TemplateResponse("home.html", {
        "request": request, 
        "pages": pages, 
        "offline": not db_instance.is_connected, 
        "branch": branch,
        "branches": branches
    })

@app.get("/page/{title}", response_class=HTMLResponse)
async def get_page(request: Request, title: str, branch: str = "main"):
    try:
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing page: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

        pages_collection = get_pages_collection()
        branches_collection = get_branches_collection()
        
        # Get available branches
        branches = ["main"]
        if branches_collection is not None:
            branch_docs = await branches_collection.find().to_list(100)
            branches = list(set(["main"] + [doc["branch_name"] for doc in branch_docs]))
        
        if pages_collection is not None:
            page = await pages_collection.find_one({"title": title, "branch": branch})
            if not page:
                logger.info(f"Page not found - viewing edit page: {title} on branch: {branch}")
                return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": False, "branch": branch, "branches": branches})

            page["html_content"] = markdown.markdown(page["content"])
            logger.info(f"Page viewed: {title} on branch: {branch}")
            return templates.TemplateResponse("page.html", {"request": request, "page": page, "branch": branch, "offline": False, "branches": branches})
        else:
            logger.error(f"Pages collection not available - viewing page: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True, "branch": branch, "branches": branches})
    except Exception as e:
        logger.error(f"Error viewing page {title} on branch {branch}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True, "branch": branch})

@app.get("/edit/{title}", response_class=HTMLResponse)
async def edit_page(request: Request, title: str, branch: str = "main"):
    try:
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - editing page: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

        pages_collection = get_pages_collection()
        branches_collection = get_branches_collection()
        
        # Get available branches
        branches = ["main"]
        if branches_collection is not None:
            branch_docs = await branches_collection.find().to_list(100)
            branches = list(set(["main"] + [doc["branch_name"] for doc in branch_docs]))
        
        content = ""
        if pages_collection is not None:
            page = await pages_collection.find_one({"title": title, "branch": branch})
            content = page["content"] if page else ""

        logger.info(f"Page edit accessed: {title} on branch: {branch}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": content, "branch": branch, "offline": not db_instance.is_connected, "branches": branches})
    except Exception as e:
        logger.error(f"Error accessing edit page {title} on branch {branch}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True})

@app.post("/edit/{title}")
async def save_page(title: str, content: str = Form(...), author: str = Form("Anonymous"), branch: str = Form("main")):
    try:
        if not db_instance.is_connected:
            logger.error(f"Database not connected - saving page: {title} on branch: {branch}")
            return {"error": "Database not available"}

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()

        if pages_collection is None:
            logger.error(f"Pages collection not available - saving page: {title} on branch: {branch}")
            return {"error": "Database not available"}

        existing_page = await pages_collection.find_one({"title": title, "branch": branch})

        if existing_page:
            # Save to history
            if history_collection is not None:
                history_item = {
                    "title": title,
                    "content": existing_page["content"],
                    "author": existing_page.get("author", "Anonymous"),
                    "branch": branch,
                    "updated_at": existing_page["updated_at"]
                }
                await history_collection.insert_one(history_item)

            # Update page
            await pages_collection.update_one(
                {"title": title, "branch": branch},
                {"$set": {"content": content, "author": author, "updated_at": datetime.now(timezone.utc)}}
            )
            logger.info(f"Page edited: {title} on branch: {branch} by {author}")
        else:
            # Create new page
            await pages_collection.insert_one({
                "title": title,
                "content": content,
                "author": author,
                "branch": branch,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            })
            logger.info(f"Page created: {title} on branch: {branch} by {author}")

        return RedirectResponse(url=f"/page/{title}?branch={branch}&updated=true", status_code=303)
    except Exception as e:
        logger.error(f"Error saving page {title} on branch {branch}: {str(e)}")
        return {"error": f"Failed to save page: {str(e)}"}

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "", branch: str = "main"):
    try:
        if not db_instance.is_connected:
            logger.warning("Database not connected - search attempted")
            return templates.TemplateResponse("search.html", {"request": request, "pages": [], "query": q, "offline": True})

        pages_collection = get_pages_collection()
        branches_collection = get_branches_collection()
        
        # Get available branches
        branches = ["main"]
        if branches_collection is not None:
            branch_docs = await branches_collection.find().to_list(100)
            branches = list(set(["main"] + [doc["branch_name"] for doc in branch_docs]))
        
        pages = []

        if q and pages_collection is not None:
            pages = await pages_collection.find({"$and": [
                {"branch": branch},
                {"$or": [
                    {"title": {"$regex": q, "$options": "i"}},
                    {"content": {"$regex": q, "$options": "i"}}
                ]}
            ]}).to_list(100)
            logger.info(f"Search performed: '{q}' on branch '{branch}' - found {len(pages)} results")
        else:
            logger.info("Search accessed without query")

        return templates.TemplateResponse("search.html", {"request": request, "pages": pages, "query": q, "branch": branch, "offline": not db_instance.is_connected, "branches": branches})
    except Exception as e:
        logger.error(f"Error during search '{q}' on branch '{branch}': {str(e)}")
        return templates.TemplateResponse("search.html", {"request": request, "pages": [], "query": q, "offline": True})

@app.get("/history/{title}", response_class=HTMLResponse)
async def page_history(request: Request, title: str, branch: str = "main"):
    try:
        # Sanitize title to prevent path traversal or other issues
        if not is_valid_title(title):
            logger.warning(f"Invalid title for history: {title} on branch: {branch}")
            return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "error": "Invalid page title"})

        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing history: {title} on branch: {branch}")
            return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "offline": True})

        pages_collection = get_pages_collection()
        branches_collection = get_branches_collection()
        history_collection = get_history_collection()
        
        # Get available branches
        branches = ["main"]
        if branches_collection is not None:
            branch_docs = await branches_collection.find().to_list(100)
            branches = list(set(["main"] + [doc["branch_name"] for doc in branch_docs]))
        
        versions = []
        
        try:
            if history_collection is not None:
                # Get history versions for the specific branch
                versions = await history_collection.find({"title": title, "branch": branch}).sort("updated_at", -1).to_list(100)
                # Get current version for the specific branch
                if pages_collection is not None:
                    current = await pages_collection.find_one({"title": title, "branch": branch})
                    if current:
                        versions.insert(0, current)  # Add current version at the beginning
        except Exception as db_error:
            logger.error(f"Database error while fetching history for {title} on branch {branch}: {str(db_error)}")
            return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "error": "Database error occurred"})

        logger.info(f"History viewed: {title} on branch: {branch}")
        return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": versions, "branch": branch, "offline": not db_instance.is_connected, "branches": branches})
    except Exception as e:
        logger.error(f"Error viewing history {title} on branch {branch}: {str(e)}")
        return templates.TemplateResponse("history.html", {"request": request, "title": title, "versions": [], "error": "An error occurred while loading history"})

@app.get("/history/{title}/{version_index}", response_class=HTMLResponse)
async def view_version(request: Request, title: str, version_index: int, branch: str = "main"):
    try:
        # Sanitize title to prevent path traversal or other issues
        if not is_valid_title(title):
            logger.warning(f"Invalid title for version view: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Invalid page title"})

        # Validate version index
        if version_index < 0:
            logger.warning(f"Invalid version index: {version_index} for title: {title} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Invalid version index"})

        if not db_instance.is_connected:
            logger.warning(f"Database not connected - viewing version: {title} v{version_index} on branch: {branch}")
            return templates.TemplateResponse("page.html", {"request": request, "title": title, "content": "", "offline": True})

        pages_collection = get_pages_collection()
        branches_collection = get_branches_collection()
        history_collection = get_history_collection()
        
        # Get available branches
        branches = ["main"]
        if branches_collection is not None:
            branch_docs = await branches_collection.find().to_list(100)
            branches = list(set(["main"] + [doc["branch_name"] for doc in branch_docs]))
        
        if pages_collection is None or history_collection is None:
            logger.error(f"Database collections not available - viewing version: {title} v{version_index} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Database not available"})

        page = None
        try:
            if version_index == 0:
                # Current version
                page = await pages_collection.find_one({"title": title, "branch": branch})
            else:
                # Historical version
                versions = await history_collection.find({"title": title, "branch": branch}).sort("updated_at", -1).to_list(100)
                if version_index - 1 < len(versions):
                    page = versions[version_index - 1]
        except Exception as db_error:
            logger.error(f"Database error while fetching version {version_index} for {title} on branch {branch}: {str(db_error)}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "Database error occurred"})

        if not page:
            logger.info(f"Version not found - viewing edit page: {title} v{version_index} on branch: {branch}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": False})

        try:
            page["html_content"] = markdown.markdown(page["content"])
        except Exception as md_error:
            logger.error(f"Error rendering markdown for version {version_index} of {title} on branch {branch}: {str(md_error)}")
            page["html_content"] = page["content"]  # Fallback to raw content

        logger.info(f"Version viewed: {title} v{version_index} on branch: {branch}")
        return templates.TemplateResponse("version.html", {
            "request": request, 
            "page": page, 
            "version_num": version_index,
            "version_index": version_index,
            "branch": branch,
            "offline": not db_instance.is_connected,
            "branches": branches
        })
    except Exception as e:
        logger.error(f"Error viewing version {title} v{version_index} on branch {branch}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "error": "An error occurred while loading version"})

@app.post("/restore/{title}/{version_index}")
async def restore_version(title: str, version_index: int, branch: str = "main"):
    try:
        # Sanitize title to prevent path traversal or other issues
        if not is_valid_title(title):
            logger.warning(f"Invalid title for restore: {title} on branch: {branch}")
            return RedirectResponse(url=f"/page/{title}?branch={branch}", status_code=303)

        # Validate version index
        if version_index < 0:
            logger.warning(f"Invalid version index: {version_index} for title: {title} on branch: {branch}")
            return RedirectResponse(url=f"/page/{title}?branch={branch}", status_code=303)

        if not db_instance.is_connected:
            logger.error(f"Database not connected - restoring version: {title} v{version_index} on branch: {branch}")
            return RedirectResponse(url=f"/page/{title}?branch={branch}&error=database_not_available", status_code=303)

        pages_collection = get_pages_collection()
        history_collection = get_history_collection()
        
        if pages_collection is None or history_collection is None:
            logger.error(f"Database collections not available - restoring version: {title} v{version_index} on branch: {branch}")
            return RedirectResponse(url=f"/page/{title}?branch={branch}&error=database_not_available", status_code=303)

        page = None
        try:
            if version_index == 0:
                # Current version - nothing to restore
                logger.info(f"Attempt to restore current version (no action): {title} v{version_index} on branch: {branch}")
                return RedirectResponse(url=f"/page/{title}?branch={branch}", status_code=303)
            else:
                # Historical version
                versions = await history_collection.find({"title": title, "branch": branch}).sort("updated_at", -1).to_list(100)
                if version_index - 1 < len(versions):
                    page = versions[version_index - 1]
        except Exception as db_error:
            logger.error(f"Database error while fetching version {version_index} for restore {title} on branch {branch}: {str(db_error)}")
            return RedirectResponse(url=f"/page/{title}?branch={branch}&error=database_error", status_code=303)
        
        if not page:
            logger.error(f"Version not found for restore: {title} v{version_index} on branch: {branch}")
            return RedirectResponse(url=f"/page/{title}?branch={branch}&error=version_not_found", status_code=303)

        try:
            # Save current version to history before restoring
            current_page = await pages_collection.find_one({"title": title, "branch": branch})
            if current_page:
                history_item = {
                    "title": title,
                    "content": current_page["content"],
                    "author": current_page.get("author", "Anonymous"),
                    "branch": branch,
                    "updated_at": current_page["updated_at"]
                }
                await history_collection.insert_one(history_item)

            # Restore the version
            await pages_collection.update_one(
                {"title": title, "branch": branch},
                {"$set": {
                    "content": page["content"],
                    "author": page.get("author", "Anonymous"),
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
        except Exception as db_error:
            logger.error(f"Database error while restoring version {version_index} of {title} on branch {branch}: {str(db_error)}")
            return RedirectResponse(url=f"/page/{title}?branch={branch}&error=restore_failed", status_code=303)

        logger.info(f"Version restored: {title} v{version_index} on branch: {branch}")
        return RedirectResponse(url=f"/page/{title}?branch={branch}&restored=true", status_code=303)
    except Exception as e:
        logger.error(f"Error restoring version {title} v{version_index} on branch {branch}: {str(e)}")
        return RedirectResponse(url=f"/page/{title}?branch={branch}&error=restore_error", status_code=303)

@app.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    try:
        # Create uploads directory if it doesn't exist
        upload_dir = "static/uploads"
        os.makedirs(upload_dir, exist_ok=True)
        
        # Validate file type
        allowed_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
        if file.content_type not in allowed_types:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid file type. Only JPEG, PNG, GIF, and WebP images are allowed."}
            )
        
        # Validate file size (max 5MB)
        contents = await file.read()
        if len(contents) > 5 * 1024 * 1024:  # 5MB
            return JSONResponse(
                status_code=400,
                content={"error": "File too large. Maximum file size is 5MB."}
            )
        
        # Reset file pointer
        await file.seek(0)
        
        # Generate unique filename
        file_extension = file.filename.split(".")[-1] if "." in file.filename else ""
        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        file_path = os.path.join(upload_dir, unique_filename)
        
        # Save file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Return success response with image URL
        image_url = f"/static/uploads/{unique_filename}"
        return JSONResponse(
            status_code=200,
            content={"url": image_url, "filename": unique_filename}
        )
    except Exception as e:
        logger.error(f"Error uploading image: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to upload image"}
        )

@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, branch: str = "main"):
    """Display wiki statistics page."""
    try:
        if not db_instance.is_connected:
            logger.warning("Database not connected - viewing stats")
            return templates.TemplateResponse("stats.html", {"request": request, "offline": True, "branch": branch})

        pages_collection = get_pages_collection()
        branches_collection = get_branches_collection()
        
        # Get available branches
        branches = ["main"]
        if branches_collection is not None:
            branch_docs = await branches_collection.find().to_list(100)
            branches = list(set(["main"] + [doc["branch_name"] for doc in branch_docs]))
        
        # Get statistics from our stats module
        stats = await get_stats()
        
        logger.info("Stats page viewed")
        return templates.TemplateResponse("stats.html", {
            "request": request,
            "total_edits": stats["total_edits"],
            "total_characters": stats["total_characters"],
            "total_pages": stats["total_pages"],
            "total_images": stats["total_images"],
            "last_updated": stats["last_updated"],
            "offline": False,
            "branch": branch,
            "branches": branches
        })
    except Exception as e:
        logger.error(f"Error viewing stats page: {str(e)}")
        return templates.TemplateResponse("stats.html", {"request": request, "offline": True, "branch": branch})

@app.get("/branches/{title}", response_class=HTMLResponse)
async def list_branches(request: Request, title: str, branch: str = "main"):
    """List all branches for a page."""
    try:
        if not db_instance.is_connected:
            logger.warning(f"Database not connected - listing branches for: {title}")
            return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True, "branch": branch})

        branches_collection = get_branches_collection()
        pages_collection = get_pages_collection()
        branches = []
        
        if branches_collection is not None:
            # Get all branches for this page
            branch_docs = await branches_collection.find({"page_title": title}).to_list(100)
            branches = [doc["branch_name"] for doc in branch_docs]
            
            # Also check pages collection for any branches that might not be in branches collection
            if pages_collection is not None:
                page_docs = await pages_collection.find({"title": title}).to_list(100)
                page_branches = [doc["branch"] for doc in page_docs if "branch" in doc]
                # Merge and deduplicate
                branches = list(set(branches + page_branches))
        
        logger.info(f"Branches listed for page: {title}")
        return templates.TemplateResponse("edit.html", {
            "request": request, 
            "title": title, 
            "content": "", 
            "branches": branches, 
            "offline": not db_instance.is_connected,
            "branch": branch
        })
    except Exception as e:
        logger.error(f"Error listing branches for {title}: {str(e)}")
        return templates.TemplateResponse("edit.html", {"request": request, "title": title, "content": "", "offline": True, "branch": branch})

@app.post("/branches/{title}/create")
async def create_branch(title: str, branch_name: str = Form(...), source_branch: str = Form("main")):
    """Create a new branch for a page."""
    try:
        if not db_instance.is_connected:
            logger.error(f"Database not connected - creating branch: {branch_name} for page: {title}")
            return {"error": "Database not available"}

        pages_collection = get_pages_collection()
        branches_collection = get_branches_collection()
        history_collection = get_history_collection()

        if pages_collection is None or branches_collection is None:
            logger.error(f"Database collections not available - creating branch: {branch_name} for page: {title}")
            return {"error": "Database not available"}

        # Check if branch already exists
        existing_branch = await branches_collection.find_one({"page_title": title, "branch_name": branch_name})
        if existing_branch:
            logger.warning(f"Branch already exists: {branch_name} for page: {title}")
            return {"error": "Branch already exists"}

        # Get source page
        source_page = await pages_collection.find_one({"title": title, "branch": source_branch})
        if not source_page:
            logger.error(f"Source page not found: {title} on branch: {source_branch}")
            return {"error": "Source page not found"}

        # Create new branch entry
        await branches_collection.insert_one({
            "page_title": title,
            "branch_name": branch_name,
            "created_at": datetime.now(timezone.utc),
            "created_from": source_branch
        })

        # Copy page to new branch
        new_page = source_page.copy()
        # Remove the _id field to avoid duplicate key error
        new_page.pop("_id", None)
        new_page["branch"] = branch_name
        new_page["created_at"] = datetime.now(timezone.utc)
        new_page["updated_at"] = datetime.now(timezone.utc)
        await pages_collection.insert_one(new_page)

        # Copy history to new branch
        if history_collection is not None:
            source_history = await history_collection.find({"title": title, "branch": source_branch}).to_list(100)
            for history_item in source_history:
                new_history_item = history_item.copy()
                # Remove the _id field to avoid duplicate key error
                new_history_item.pop("_id", None)
                new_history_item["branch"] = branch_name
                await history_collection.insert_one(new_history_item)

        logger.info(f"Branch created: {branch_name} for page: {title} from branch: {source_branch}")
        return RedirectResponse(url=f"/page/{title}?branch={branch_name}", status_code=303)
    except Exception as e:
        logger.error(f"Error creating branch {branch_name} for page {title}: {str(e)}")
        return {"error": f"Failed to create branch: {str(e)}"}

@app.post("/set-branch")
async def set_branch(request: Request, branch: str = Form(...)):
    """Set the global branch for the session."""
    # In a real application, you would store this in a session or cookie
    # For now, we'll just redirect back to the referring page with the branch parameter
    referer = request.headers.get("referer", "/")
    if "?" in referer:
        if "branch=" in referer:
            # Replace existing branch parameter
            import re
            referer = re.sub(r'branch=[^&]*', f'branch={branch}', referer)
        else:
            # Add branch parameter
            referer += f"&branch={branch}"
    else:
        referer += f"?branch={branch}"
    
    logger.info(f"Branch set to: {branch}")
    return RedirectResponse(url=referer, status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

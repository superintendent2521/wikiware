"""
Page routes for WikiWare.
Handles user-specific page viewing, editing, and saving operations.
"""

import markdown

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ..database import db_instance
from ..middleware.auth_middleware import AuthMiddleware
from ..services.page_service import PageService
from ..utils.link_processor import process_internal_links
from ..utils.sanitizer import sanitize_html
from ..utils.template_env import get_templates
from ..utils.validation import is_safe_branch_parameter, is_valid_title

router = APIRouter()

templates = get_templates()


def _build_user_page_redirect_url(request: Request, username: str, branch: str) -> str:
    """Build an internal URL for the user page route with optional branch."""
    target_url = request.url_for("user_page", username=username)
    if branch != "main":
        target_url = target_url.include_query_params(branch=branch)
    return str(target_url)


@router.get("/user/{username}", response_class=HTMLResponse)
async def user_page(
    request: Request,
    response: Response,
    username: str,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """View a user's personal page."""
    current_user = await AuthMiddleware.get_current_user(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    csrf_protect.set_csrf_cookie(signed_token, response)

    if not db_instance.is_connected:
        template = templates.TemplateResponse(
            "user.html",
            {
                "request": request,
                "username": username,
                "content": "",
                "offline": True,
                "user": current_user,
                "is_owner": False,
                "csrf_token": csrf_token,
                "is_user_page": True,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template

    # Get the user's page
    page = await PageService.get_page(username, branch)

    if not page:
        # If user page doesn't exist, create a placeholder
        page = {
            "title": username,
            "content": f"# Welcome to {username}'s page\n\nThis is your personal page. Edit it to introduce yourself!",
            "author": "",
            "updated_at": "",
            "branch": branch,
        }

    # Process internal links and render as Markdown
    processed_content = await process_internal_links(page["content"])
    md = markdown.Markdown(extensions=["tables"])
    page["html_content"] = sanitize_html(md.convert(processed_content))

    # Check if current user is the owner of this page
    is_owner = current_user and current_user["username"] == username

    # Get user statistics
    user_stats = None
    users_collection = db_instance.get_collection("users")
    if users_collection is not None:
        user_doc = await users_collection.find_one({"username": username})
        if user_doc:
            user_stats = {
                "total_edits": user_doc.get("total_edits", 0),
                "page_edits": user_doc.get("page_edits", {}),
            }

    template = templates.TemplateResponse(
        "user.html",
        {
            "request": request,
            "username": username,
            "page": page,
            "offline": not db_instance.is_connected,
            "branch": branch,
            "user": current_user,
            "is_owner": is_owner,
            "csrf_token": csrf_token,
            "user_stats": user_stats,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


@router.get("/user/{username}/edit", response_class=HTMLResponse)
async def edit_user_page(
    request: Request,
    response: Response,
    username: str,
    branch: str = "main",
    csrf_protect: CsrfProtect = Depends(),
):
    """Edit a user's personal page."""
    user = await AuthMiddleware.require_auth(request)
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    csrf_protect.set_csrf_cookie(signed_token, response)

    if not db_instance.is_connected:
        template = templates.TemplateResponse(
            "edit.html",
            {
                "request": request,
                "title": username,
                "content": "",
                "offline": True,
                "user": user,
                "csrf_token": csrf_token,
                "is_user_page": True,
            },
        )
        csrf_protect.set_csrf_cookie(signed_token, template)
        return template

    # Only allow editing own page
    if user["username"] != username:
        raise HTTPException(
            status_code=403, detail="You can only edit your own user page"
        )

    # Get existing content
    content = ""
    page = await PageService.get_page(username, branch)
    if page:
        content = page["content"]

    template = templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "title": username,
            "content": content,
            "branch": branch,
            "offline": not db_instance.is_connected,
            "user": user,
            "csrf_token": csrf_token,
            "is_user_page": True,
        },
    )
    csrf_protect.set_csrf_cookie(signed_token, template)
    return template


@router.post("/user/{username}/edit")
async def save_user_page(
    request: Request,
    username: str,
    content: str = Form(...),
    branch: str = Form("main"),
    edit_summary: str = Form(...),
):
    """Save user page changes."""
    try:
        user = await AuthMiddleware.require_auth(request)

        if not db_instance.is_connected:
            return {"error": "Database not available"}

        # Only allow editing own page
        if user["username"] != username:
            raise HTTPException(
                status_code=403, detail="You can only edit your own user page"
            )

        # Validate title (username) - must be safe for path inclusion
        import re

        if not is_valid_title(username) or not re.match(r"^[a-zA-Z0-9_-]+$", username):
            raise HTTPException(status_code=400, detail="Invalid username")

        if not is_safe_branch_parameter(branch):
            logger.warning(
                f"Invalid branch '{branch}' while saving user page for {username}, defaulting to main"
            )
            branch = "main"

        # Use the authenticated user as the author
        author = user["username"]

        # Save the page
        success = await PageService.update_page(
            username, content, author, branch, edit_summary=edit_summary
        )

        if success:
            redirect_url = _build_user_page_redirect_url(request, username, branch)
            return RedirectResponse(url=redirect_url, status_code=303)
        else:
            return {"error": "Failed to save user page"}
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error saving user page {username} on branch {branch}: {str(e)}")
        return {"error": "Failed to save user page, try again later."}

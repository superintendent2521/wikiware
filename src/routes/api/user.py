"""
Page routes for WikiWare.
Handles user-specific page editing operations (API).
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.page_service import PageService
from ...utils.validation import is_safe_branch_parameter, is_valid_title

router = APIRouter()


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
            redirect_url = request.url_for("user_page", username=username)
            if branch != "main" and is_safe_branch_parameter(branch):
                redirect_url = redirect_url.include_query_params(branch=branch)
            return RedirectResponse(url=str(redirect_url), status_code=303)
        else:
            return {"error": "Failed to save user page"}
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error saving user page {username} on branch {branch}: {str(e)}")
        return {"error": "Failed to save user page, try again later."}

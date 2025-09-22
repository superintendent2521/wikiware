"""
Page routes for WikiWare.
Handles page editing and management operations (API).
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ...database import db_instance
from ...middleware.auth_middleware import AuthMiddleware
from ...services.page_service import PageService
from ...utils.validation import is_safe_branch_parameter, is_valid_title

router = APIRouter()


def _build_page_redirect_url(request: Request, title: str, branch: str) -> str:
    """Build an internal URL for the page route with proper encoding."""
    target_url = request.url_for("get_page", title=title)
    if branch != "main":
        target_url = target_url.include_query_params(branch=branch)
    return str(target_url)


async def _is_user_page_title(title: str) -> bool:
    """Return True if the title matches an existing user's personal page."""
    if not title or not db_instance.is_connected:
        return False
    users_collection = db_instance.get_collection("users")
    if users_collection is None:
        return False
    # Perform a single case-insensitive query for the username.
    import re
    regex = {"$regex": f"^{re.escape(title)}$", "$options": "i"}
    user_doc = await users_collection.find_one({"username": regex})
    return user_doc is not None


@router.post("/edit/{title}")
async def save_page(
    request: Request,
    title: str,
    content: str = Form(...),
    author: str = Form("Anonymous"),
    branch: str = Form("main"),
    edit_summary: str = Form(...),
):
    """Save page changes."""
    try:
        # Check if user is authenticated
        user = await AuthMiddleware.require_auth(request)

        if (
            await _is_user_page_title(title)
            and user["username"].casefold() != title.casefold()
        ):
            logger.warning(
                f"User {user['username']} attempted to save personal page {title} via generic editor"
            )
            redirect_url = request.url_for("user_page", username=title)
            if branch != "main" and is_safe_branch_parameter(branch):
                redirect_url = redirect_url.include_query_params(branch=branch)
            return RedirectResponse(url=str(redirect_url), status_code=303)

        if not db_instance.is_connected:
            logger.error(
                f"Database not connected - saving page: {title} on branch: {branch}"
            )
            return {"error": "Database not available"}

        # Validate title
        if not is_valid_title(title):
            raise HTTPException(status_code=400, detail="Invalid page title")

        if not is_safe_branch_parameter(branch):
            raise HTTPException(status_code=400, detail="Invalid branch")

        # Use the authenticated user as the author
        author = user["username"]

        # Save the page
        success = await PageService.update_page(
            title, content, author, branch, edit_summary=edit_summary
        )

        if success:
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)
        else:
            return {"error": "Failed to save page"}
    except HTTPException:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error saving page {title} on branch {branch}: {str(e)}")
        return {"error": "Failed to save page"}


@router.post("/delete/{title}")
async def delete_page(
    request: Request, title: str, csrf_protect: CsrfProtect = Depends()
):
    """Delete a page (all branches)."""
    try:
        # Validate CSRF token (reads token from body per config and cookie)
        # Add extra diagnostics in logs to help track issues
        form_data = await request.form()
        csrf_token = form_data.get("csrf_token")
        logger.debug(
            f"Delete page '{title}' csrf_token in form present={bool(csrf_token)}; cookies keys={list(request.cookies.keys())}"
        )
        logger.debug(
            f"CSRF cookie value present={bool(request.cookies.get('fastapi-csrf-token'))}"
        )
        await csrf_protect.validate_csrf(request)

        # Check if user is authenticated and is an admin
        user = await AuthMiddleware.require_auth(request)
        if not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="Admin privileges required")

        if not db_instance.is_connected:
            logger.error(f"Database not connected - cannot delete page: {title}")
            return {"error": "Database not available"}

        # Use PageService to delete the page (all branches)
        success = await PageService.delete_page(title)

        if success:
            logger.info(
                f"Page deleted (all branches): {title} by admin {user['username']}"
            )
            return RedirectResponse(url="/", status_code=303)
        else:
            logger.warning(f"Page not found for deletion: {title}")
            return {"error": "Page not found"}

    except HTTPException:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error deleting page {title}: {str(e)}")
        return {"error": "Failed to delete page"}


@router.post("/delete-branch/{title}")
async def delete_branch(
    request: Request,
    title: str,
    branch: str = Form("main"),
    csrf_protect: CsrfProtect = Depends(),
):
    """Delete a specific branch from a page."""
    try:
        # Validate CSRF token (reads token from body per config and cookie)
        # Add extra diagnostics in logs to help track issues
        form_data = await request.form()
        csrf_token = form_data.get("csrf_token")
        logger.debug(
            f"Delete branch '{branch}' from page '{title}' csrf_token in form present={bool(csrf_token)}; cookies keys={list(request.cookies.keys())}"
        )
        logger.debug(
            f"CSRF cookie value present={bool(request.cookies.get('fastapi-csrf-token'))}"
        )
        await csrf_protect.validate_csrf(request)

        # Check if user is authenticated and is an admin
        user = await AuthMiddleware.require_auth(request)
        if not user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="Admin privileges required")

        if not db_instance.is_connected:
            logger.error(
                f"Database not connected - cannot delete branch {branch} from page {title}"
            )
            return {"error": "Database not available"}

        # Use PageService to delete the branch from the page
        success = await PageService.delete_branch(title, branch)

        if success:
            logger.info(
                f"Branch deleted from page: {branch} from {title} by admin {user['username']}"
            )
            # Validate before using in redirect URL
            if not is_valid_title(title) or not is_safe_branch_parameter(branch):
                logger.warning(
                    f"Attempted redirect with invalid title '{title}' or branch '{branch}'"
                )
                return RedirectResponse(url="/", status_code=303)
            redirect_url = _build_page_redirect_url(request, title, branch)
            return RedirectResponse(url=redirect_url, status_code=303)
        else:
            logger.warning(f"Branch not found for deletion: {branch} from page {title}")
            return {"error": "Branch not found"}

    except HTTPException:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Error deleting branch {branch} from page {title}: {str(e)}")
        return {"error": "Failed to delete branch"}

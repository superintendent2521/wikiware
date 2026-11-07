"""CSRF validation for JSON and form API mutations."""

from fastapi import Depends, HTTPException, Request
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError


async def require_csrf(request: Request, csrf_protect: CsrfProtect = Depends()):
    try:
        await csrf_protect.validate_csrf(request)
    except CsrfProtectError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

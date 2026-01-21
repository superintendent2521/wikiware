from ...services.page_service import PageService
from fastapi import APIRouter, HTTPException


router = APIRouter()


@router.get("/page/markdown/{title}")
async def get_markdown_page(title: str):
    """Fetches the markdown content of a page by its title. useful for ai clients."""
    page = await PageService.get_page(title, "main")
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")

    return {
        "title": page.get("title"),
        "content": page.get("content"),
        "branch": page.get("branch"),
        "author": page.get("author"),
    }

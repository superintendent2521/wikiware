"""API endpoints for PDF generation from Markdown files."""


from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from loguru import logger
import io

from markdown_pdf import MarkdownPdf, Section

from ...services.page_service import PageService

router = APIRouter()

class PDFRequest(BaseModel):
    title: str
    branch: str = "main"


@router.post("/pdf/page")
async def generate_page_pdf(request: Request, pdf_req: PDFRequest):
    """Generate PDF for an authenticated user's requested page."""

    #user = await AuthMiddleware.require_auth(request)
    #username = user["username"]
    #logger.info(f"Generating PDF for page '{pdf_req.title}' (branch: {pdf_req.branch}) by user {username}")
    page = await PageService.get_page(pdf_req.title, pdf_req.branch)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    content = page["content"]
    logger.info(f"Generating PDF for page '{pdf_req.title}' (branch: {pdf_req.branch}) by user ")

    try:
        import io
        from markdown_pdf import MarkdownPdf, Section

        pdf = MarkdownPdf()
        section = Section(content)
        pdf.add_section(section)
        out = io.BytesIO()
        pdf.save(out)
        pdf_bytes = out.getvalue()
    except Exception as e:
        logger.error(f"Error generating PDF for {pdf_req.title}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to generate PDF")

    pdf_io = io.BytesIO(pdf_bytes)
    filename = f"{pdf_req.title.replace(' ', '_')}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

    return StreamingResponse(pdf_io, media_type="application/pdf", headers=headers)

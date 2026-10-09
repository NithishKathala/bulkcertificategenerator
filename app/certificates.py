"""PDF certificate rendering."""

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas


def output_directory() -> Path:
    directory = Path(os.getenv("CERTIFICATE_OUTPUT_DIR", "generated")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def generate_certificate(
    *, recipient_name: str, course_name: str, completion_date: str, job_id: str, certificate_id: str
) -> Path:
    """Create one certificate PDF and return its path."""
    path = output_directory() / job_id / f"{certificate_id}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)

    page_width, page_height = landscape(letter)
    pdf = canvas.Canvas(str(path), pagesize=(page_width, page_height))
    pdf.setTitle(f"Certificate - {recipient_name}")
    pdf.setStrokeColor(colors.HexColor("#1B4965"))
    pdf.setLineWidth(4)
    pdf.roundRect(28, 28, page_width - 56, page_height - 56, 14, stroke=1, fill=0)

    pdf.setFillColor(colors.HexColor("#1B4965"))
    pdf.setFont("Helvetica-Bold", 30)
    pdf.drawCentredString(page_width / 2, page_height - 130, "CERTIFICATE OF COMPLETION")
    pdf.setFillColor(colors.HexColor("#333333"))
    pdf.setFont("Helvetica", 16)
    pdf.drawCentredString(page_width / 2, page_height - 195, "This certificate is presented to")
    pdf.setFillColor(colors.HexColor("#1B4965"))
    pdf.setFont("Helvetica-Bold", 28)
    pdf.drawCentredString(page_width / 2, page_height - 245, recipient_name[:80])
    pdf.setFillColor(colors.HexColor("#333333"))
    pdf.setFont("Helvetica", 16)
    pdf.drawCentredString(page_width / 2, page_height - 295, f"for successfully completing {course_name[:100]}")
    pdf.setFont("Helvetica", 12)
    pdf.drawCentredString(page_width / 2, 110, f"Completion date: {completion_date}")
    pdf.setStrokeColor(colors.HexColor("#777777"))
    pdf.line(page_width / 2 - 110, 78, page_width / 2 + 110, 78)
    pdf.setFont("Helvetica", 10)
    pdf.drawCentredString(page_width / 2, 62, "Authorized signature")
    pdf.save()
    return path

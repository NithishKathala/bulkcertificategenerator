"""PDF certificate rendering."""

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.pdfgen import canvas


def output_directory() -> Path:
    directory = Path(os.getenv("CERTIFICATE_OUTPUT_DIR", "generated")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def certificate_verification_url(certificate_id: str) -> str:
    """Build the URL embedded in certificates; configure the public host before generation."""
    base_url = os.getenv("CERTIFICATE_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base_url}/api/certificates/{certificate_id}/verify"


def generate_certificate(
    *, recipient_name: str, course_name: str, completion_date: str,
    job_id: str, certificate_id: str, certificate_type: str = "completion"
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
    is_completion = certificate_type == "completion"
    pdf.setFont("Helvetica-Bold", 30)
    title = "CERTIFICATE OF COMPLETION" if is_completion else "CERTIFICATE OF PARTICIPATION"
    pdf.drawCentredString(page_width / 2, page_height - 130, title)
    pdf.setFillColor(colors.HexColor("#333333"))
    pdf.setFont("Helvetica", 16)
    pdf.drawCentredString(page_width / 2, page_height - 195, "This certificate is presented to")
    pdf.setFillColor(colors.HexColor("#1B4965"))
    pdf.setFont("Helvetica-Bold", 28)
    pdf.drawCentredString(page_width / 2, page_height - 245, recipient_name[:80])
    pdf.setFillColor(colors.HexColor("#333333"))
    pdf.setFont("Helvetica", 16)
    description = "for successfully completing" if is_completion else "for participating in"
    pdf.drawCentredString(page_width / 2, page_height - 295, f"{description} {course_name[:100]}")
    pdf.setFont("Helvetica", 12)
    pdf.drawCentredString(page_width / 2, 110, f"Completion date: {completion_date}")
    pdf.setStrokeColor(colors.HexColor("#777777"))
    pdf.line(page_width / 2 - 110, 78, page_width / 2 + 110, 78)
    pdf.setFont("Helvetica", 10)
    pdf.drawCentredString(page_width / 2, 62, "Authorized signature")

    pdf.setFillColor(colors.HexColor("#333333"))
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(page_width / 2, 42, f"Certificate ID: {certificate_id}")

    qr_widget = qr.QrCodeWidget(certificate_verification_url(certificate_id))
    x1, y1, x2, y2 = qr_widget.getBounds()
    qr_size = 82
    qr_drawing = Drawing(
        qr_size,
        qr_size,
        transform=[qr_size / (x2 - x1), 0, 0, qr_size / (y2 - y1), 0, 0],
    )
    qr_drawing.add(qr_widget)
    qr_x = page_width - qr_size - 70
    renderPDF.draw(qr_drawing, pdf, qr_x, 43)
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(qr_x + qr_size / 2, 34, "Scan to verify")

    pdf.save()
    return path

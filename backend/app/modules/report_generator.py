from __future__ import annotations

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


class ReportGenerator:
    def generate_pdf(self, report: dict) -> bytes:
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        y = height - 50

        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(40, y, "AI Voice Interview Report")
        y -= 28

        pdf.setFont("Helvetica", 11)
        pdf.drawString(40, y, f"Candidate: {report['candidate_name']}")
        y -= 18
        pdf.drawString(40, y, f"Mode: {report['mode']}")
        y -= 18
        pdf.drawString(40, y, f"Overall Score: {report['overall_score']}%")
        y -= 26

        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(40, y, "Question Breakdown")
        y -= 18

        pdf.setFont("Helvetica", 10)
        for index, item in enumerate(report["items"], start=1):
            for line in [
                f"{index}. {item['question']}",
                f"Score: {item['score']}%",
                f"Feedback: {item['feedback']}",
                f"Suggestion: {item['improvement_suggestion']}",
            ]:
                if y < 70:
                    pdf.showPage()
                    y = height - 50
                    pdf.setFont("Helvetica", 10)
                pdf.drawString(40, y, line[:110])
                y -= 16
            y -= 10

        pdf.save()
        return buffer.getvalue()


report_generator = ReportGenerator()

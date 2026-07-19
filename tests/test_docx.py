from app.services.docx_service import generate_resume_docx


markdown_resume = """# Candidate Name

## Professional Summary

Financial Data Analyst with experience in reporting and analytics.
"""

filename = generate_resume_docx(
    markdown_resume,
    company="Bamboo",
    job_title="Financial Data Analyst",
)

print(filename)

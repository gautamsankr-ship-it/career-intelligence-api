from app.services.ai_service import analyze_job

job = """
Intermediate Accountant

Experience in Xero, BAS, Payroll,
Financial Statements and Australian Tax.
"""

result = analyze_job(job)

print(result)
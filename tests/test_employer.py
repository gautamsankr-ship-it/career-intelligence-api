from app.services.ai_service import analyze_job
from app.services.employer_service import EmployerService

job = analyze_job("""

Financial Data Analyst

Bamboo

We are looking for a Financial Data Analyst with
Power BI, SQL, Excel, Financial Reporting
experience.

""")

service = EmployerService()

result = service.analyze(job)

print(result)
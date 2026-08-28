"""Manual employer-analysis demo — NOT a pytest test.

Running this script issues a LIVE OpenAI API call via analyze_job(). It must
be run deliberately from the command line only; it must never be imported or
collected by pytest, since import alone would trigger a real, billed API call.
"""

from app.services.ai_service import analyze_job
from app.services.employer_service import EmployerService


def main() -> None:
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


if __name__ == "__main__":
    main()

"""Manual AI job-analysis demo — NOT a pytest test.

Running this script issues a LIVE OpenAI API call via analyze_job(). It must
be run deliberately from the command line only; it must never be imported or
collected by pytest, since import alone would trigger a real, billed API call.
"""

from app.services.ai_service import analyze_job


def main() -> None:
    job = """
Intermediate Accountant

Experience in Xero, BAS, Payroll,
Financial Statements and Australian Tax.
"""

    result = analyze_job(job)
    print(result)


if __name__ == "__main__":
    main()

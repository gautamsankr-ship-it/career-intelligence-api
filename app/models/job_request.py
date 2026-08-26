from pydantic import BaseModel, Field

class JobRequest(BaseModel):
    job_description: str = Field(
        ...,
        example="Senior Financial Analyst role at a global firm. Responsibilities include financial modeling, budgeting, and variance analysis. Requires 5+ years of experience, Power BI proficiency, and knowledge of Australian accounting standards."
    )
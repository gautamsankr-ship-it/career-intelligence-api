from pydantic import BaseModel


class ResumeRequest(BaseModel):
    application_id: str
    candidate_name: str
    company: str
    job_title: str
    location: str
    ats_score: int
    resume_text: str
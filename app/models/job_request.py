from pydantic import BaseModel

class JobRequest(BaseModel):
    job_description: str
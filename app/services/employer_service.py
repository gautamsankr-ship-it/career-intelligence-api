import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from app.models.employer import Employer

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


class EmployerService:

    def analyze(self, job_analysis):

        company = job_analysis.get("company", "Unknown Company")

        prompt = f"""
You are a senior executive recruiter.

Evaluate this employer from the perspective of a candidate.

Company:
{company}

Job Analysis:
{json.dumps(job_analysis, indent=2)}

Return ONLY valid JSON.

{{
    "company":"",
    "industry":"",
    "company_size":"",
    "remote_friendly": true,
    "innovation_score": 0,
    "culture_score": 0,
    "career_growth_score": 0,
    "financial_stability_score": 0,
    "overall_score": 0,
    "strengths": [],
    "risks": [],
    "recommendation":"",
    "reason":""
}}
"""

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "Return only valid JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        data = json.loads(
            response.choices[0].message.content
        )

        return Employer(
            company=data.get("company", company),
            industry=data.get("industry", ""),
            company_size=data.get("company_size", ""),
            remote_friendly=data.get("remote_friendly", False),
            innovation_score=data.get("innovation_score", 0),
            culture_score=data.get("culture_score", 0),
            career_growth_score=data.get("career_growth_score", 0),
            financial_stability_score=data.get("financial_stability_score", 0),
            overall_score=data.get("overall_score", 0),
            strengths=data.get("strengths", []),
            risks=data.get("risks", []),
            recommendation=data.get("recommendation", ""),
            reason=data.get("reason", "")
        )
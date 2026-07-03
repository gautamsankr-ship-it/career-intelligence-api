import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


class ResponsibilityMatchingService:

    def match(
        self,
        candidate_experience,
        job_summary,
        keywords,
    ):

        prompt = f"""
You are an executive recruiter.

Candidate Experience:

{json.dumps(candidate_experience, indent=2)}

Job Summary:

{job_summary}

Job Keywords:

{json.dumps(keywords, indent=2)}

Determine how well the candidate's responsibilities match the role.

Consider semantic similarity.

Examples:

Financial Reporting == Monthly Reporting

Client Management == Stakeholder Management

Business Advisory == Commercial Finance

Audit == Risk & Compliance

Budgeting == FP&A

Return ONLY JSON.

{{
    "matched":[...],
    "missing":[...],
    "score":0-100
}}
"""

        response = client.chat.completions.create(

            model="gpt-4.1-mini",

            temperature=0,

            response_format={
                "type": "json_object"
            },

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

        return json.loads(
            response.choices[0].message.content
        )
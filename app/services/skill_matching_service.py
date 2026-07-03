import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


class SkillMatchingService:

    def match(
        self,
        candidate_skills,
        required_skills,
    ):

        prompt = f"""
You are an expert technical recruiter.

Candidate Skills:

{json.dumps(candidate_skills, indent=2)}

Required Skills:

{json.dumps(required_skills, indent=2)}

Match skills semantically.

Examples:

Tax Accounting == Australian Taxation

Financial Reporting == Reporting

Stakeholder Management == Client Management

Business Advisory == Advisory Services

Audit == External Audit

Excel == Advanced Excel

Power BI == BI Reporting

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
                    "content": "Return only JSON."
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
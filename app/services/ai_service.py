import os
import json

from dotenv import load_dotenv
from openai import OpenAI

# ============================================================
# Load Environment
# ============================================================

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

# ============================================================
# AI Job Analysis
# ============================================================

def analyze_job(job_description: str):
    """
    Analyze a job description and return structured JSON.
    """

    prompt = """
You are an Executive Recruitment Consultant,
ATS Specialist,
Career Coach,
HR Director.

Analyze the following job description.

Extract ALL relevant information.

Return ONLY valid JSON.

Job Description:

{job_description}

Return JSON EXACTLY in the following structure.

{{
    "company": "",
    "job_title": "",
    "location": "",
    "employment_type": "",

    "industry": "",
    "department": "",
    "seniority": "",

    "experience_required": 0,

    "education": [],

    "required_skills": [],

    "preferred_skills": [],

    "technologies": [],

    "responsibilities": [],

    "soft_skills": [],

    "finance_domains": [],

    "keywords": [],

    "salary": "",

    "remote": false,

    "summary": "",

    "match_reasoning": {{
        "must_have_skills": [],
        "nice_to_have_skills": [],
        "biggest_challenges": [],
        "ideal_candidate": ""
    }}
}}
""".format(job_description=job_description)

    response = client.chat.completions.create(

        model="gpt-4.1-mini",

        temperature=0,

        response_format={
            "type": "json_object"
        },

        messages=[

            {
                "role": "system",
                "content": """
You are one of the world's best executive recruiters.

Your task is to analyze job descriptions.

Rules:

1. Return ONLY valid JSON.

2. Never return markdown.

3. Never explain your answer.

4. If information is unavailable:

- use ""

- use []

- use 0

5. Extract ATS keywords exactly as written.

6. Infer years of experience where possible.

7. Identify the most important responsibilities.

8. Identify the ideal candidate profile.

9. Prioritize finance, accounting, AI,
data analytics, automation,
leadership and ERP technologies.

Return ONLY JSON.
"""
            },

            {
                "role": "user",
                "content": prompt
            }

        ]

    )

    content = response.choices[0].message.content

    return json.loads(content)
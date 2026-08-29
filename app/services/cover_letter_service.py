import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


def generate_cover_letter(candidate, job_analysis, decision):

    prompt = f"""
You are an expert executive career coach.

Candidate Profile:

{json.dumps(candidate, indent=2)}

Job Analysis:

{json.dumps(job_analysis, indent=2)}

Career Match Report:

{str(decision)}

Write a professional cover letter.

Structure:

- Opening: the role and the candidate's strongest fit proposition
- Evidence paragraph: the most vacancy-relevant experience
- Achievement paragraph: 1-3 strong achievements, using only facts present in the Candidate Profile above
- Employer-fit paragraph: why this candidate fits this employer/role, grounded in the Job Analysis above
- Closing: a concise call to discuss further

Requirements:

- Target approximately 300-450 words (unless the Job Analysis states a different length requirement)
- Professional, confident tone -- show evidence rather than asserting confidence (avoid lines like "I am confident I am the perfect candidate")
- Mention the company name and job title
- Do not state anything about the candidate that is not explicitly present in the Candidate Profile above -- never convert a Job Analysis requirement into a candidate claim
- Do not invent, exaggerate, or add metrics/numbers not present in the Candidate Profile
- End with a professional closing

Return plain text only.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.4,
        messages=[
            {
                "role": "system",
                "content": "You are an expert cover letter writer."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return response.choices[0].message.content
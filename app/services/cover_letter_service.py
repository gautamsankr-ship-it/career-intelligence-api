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

Requirements:

- Maximum one page
- Professional tone
- Mention company name
- Mention job title
- Highlight matching experience
- Mention key skills
- Explain why the candidate is a strong fit
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
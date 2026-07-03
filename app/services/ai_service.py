import os
import json

from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# Initialize OpenAI client
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


def analyze_job(job_description: str):
    """
    Analyze a job description using OpenAI
    and return structured JSON.
    """

    prompt = f"""
You are an Executive Recruitment Consultant, ATS Specialist,
Career Coach and HR Director.

Your task is to analyze the following job description and
extract structured information that will later be used for:

1. ATS Match Score
2. Resume Optimization
3. Cover Letter Generation
4. Interview Preparation
5. Career Recommendation

Return ONLY valid JSON.

Job Description:

{job_description}

Return JSON in the following format exactly.

{
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

    "match_reasoning": {
        "must_have_skills": [],
        "nice_to_have_skills": [],
        "biggest_challenges": [],
        "ideal_candidate": ""
    }
}
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": """
        You are a senior recruitment consultant.

        Extract information exactly as requested.

        Do not invent information.

        If a field is missing, return an empty string,
        empty array or 0.

        Return ONLY valid JSON.
        """
            },
            {
                "role": "user",
                "content": prompt
            },

        ]
    )

    content = response.choices[0].message.content

    return json.loads(content)
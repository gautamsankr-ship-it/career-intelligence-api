from app.models.resume_optimization import ResumeOptimizationResult


class ResumeOptimizer:

    def optimize(self, profile, job, decision):

        result = ResumeOptimizationResult()

        strategy = decision.resume_strategy

        # =====================================================
        # ATS Keywords
        # =====================================================

        result.ats_keywords = strategy.get("keywords", [])

        result.missing_keywords = strategy.get("improve", [])

        # =====================================================
        # Skills Ranking
        # =====================================================

        all_skills = []

        for category in profile["skills"].values():
            all_skills.extend(category)

        for category in profile["technology"].values():
            all_skills.extend(category)

        ranked = []

        for skill in all_skills:

            score = 0

            for keyword in result.ats_keywords:

                if keyword.lower() in skill.lower():

                    score += 10

            ranked.append((score, skill))

        ranked.sort(reverse=True)

        result.top_skills = [

            skill

            for _, skill in ranked[:20]

        ]

        # =====================================================
        # Projects
        # =====================================================

        project_scores = []

        for project in profile["projects"]:

            score = 0

            text = (

                project["description"]

                + " "

                + " ".join(project["skills"])

            ).lower()

            for keyword in result.ats_keywords:

                if keyword.lower() in text:

                    score += 5

            project_scores.append(

                (score, project)

            )

        project_scores.sort(

            reverse=True,

            key=lambda x: x[0]

        )

        result.top_projects = [

            project

            for _, project in project_scores[:3]

        ]

        # =====================================================
        # Achievements
        # =====================================================

        result.top_achievements = profile["achievements"][:8]

        # =====================================================
        # Responsibilities
        # =====================================================

        responsibilities = []

        for group in profile["responsibilities"].values():

            responsibilities.extend(group)

        result.top_responsibilities = responsibilities[:12]

        # =====================================================
        # Positioning
        # =====================================================

        title = job.get(

            "job_title",

            ""

        ).lower()

        if "finance" in title:

            result.career_positioning = (

                "Finance Transformation Professional"

            )

        elif "data" in title:

            result.career_positioning = (

                "Financial Data Analytics Professional"

            )

        elif "ai" in title:

            result.career_positioning = (

                "AI Automation Professional"

            )

        else:

            result.career_positioning = (

                profile["candidate"]["title"]

            )

        # =====================================================
        # Executive Summary
        # =====================================================

        summary = profile["professional_summary"]

        result.executive_summary = (

            summary["headline"]

            + " "

            + summary["career_direction"]

        )

        # =====================================================
        # Resume Intelligence
        # =====================================================

        result.strengths = [

            "Strong finance leadership",

            "Australian accounting expertise",

            "AI and automation capability",

            "Data analytics experience"

        ]

        result.concerns = [

            "Needs stronger role-specific positioning"

        ]

        result.recommendations = [

            "Emphasize relevant achievements.",

            "Prioritize matching projects.",

            "Move most relevant skills to page one."

        ]

        result.ats_before = decision.overall_score

        result.ats_after = min(

            100,

            decision.overall_score + 18

        )

        result.recruiter_score = min(

            100,

            result.ats_after + 2

        )

        result.hiring_manager_score = min(

            100,

            result.ats_after + 1

        )

        result.confidence = decision.confidence

        return result
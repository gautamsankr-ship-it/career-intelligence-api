from app.services.ats.keyword_extractor import KeywordExtractor
from app.services.ats.keyword_matcher import KeywordMatcher
from app.services.ats.keyword_optimizer import KeywordOptimizer
from app.services.ats.section_optimizer import SectionOptimizer
from app.services.ats.ats_score import ATSScore


class ATSEngine:
    """
    ATS Intelligence Engine

    Orchestrates all ATS modules.

    Pipeline

    Job
        ↓
    Keyword Extraction
        ↓
    Keyword Matching
        ↓
    ATS Scoring
        ↓
    Keyword Optimization
        ↓
    Section Optimization
    """

    def __init__(self):

        self.extractor = KeywordExtractor()

        self.matcher = KeywordMatcher()

        self.scorer = ATSScore()

        self.keyword_optimizer = KeywordOptimizer()

        self.section_optimizer = SectionOptimizer()

    # ==========================================================
    # Analyze Job
    # ==========================================================

    def analyze(

        self,

        job_analysis

    ):

        keywords = self.extractor.extract(

            job_analysis

        )

        matches = self.matcher.match(

            keywords

        )

        ats = self.scorer.calculate(

            keywords,

            self.matcher

        )

        keyword_actions = self.keyword_optimizer.optimize(

            matches

        )

        section_actions = self.section_optimizer.optimize(

            ats,

            keyword_actions

        )

        return {

            "ats_score": ats,

            "keyword_summary": {

                "matched":

                    matches["matched"],

                "partial":

                    matches["partial"],

                "missing":

                    matches["missing"],

                "coverage":

                    matches["coverage"],

                "statistics":

                    matches["statistics"]

            },

            "keyword_recommendations":

                keyword_actions,

            "section_recommendations":

                section_actions

        }

    # ==========================================================
    # Dashboard Summary
    # ==========================================================

    def summary(

        self,

        ats_result

    ):

        score = ats_result["ats_score"]

        stats = ats_result["keyword_summary"]["statistics"]

        return {

            "overall_score":

                score["overall_score"],

            "recommendation":

                score["recommendation"],

            "matched_keywords":

                stats["matched"],

            "partial_keywords":

                stats["partial"],

            "missing_keywords":

                stats["missing"]

        }
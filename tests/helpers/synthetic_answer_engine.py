"""Synthetic-only answer resolver for localhost browser transport tests."""
from app.models.application_answer import AnswerDecision


class SyntheticAnswerEngine:
    _facts={
        "first name": ("FIRST_NAME", "Test"),
        "last name": ("LAST_NAME", "Candidate"),
        "email address": ("EMAIL_ADDRESS", "test.candidate@example.invalid"),
        "phone": ("PHONE_NUMBER", "+447000000000"),
        "notice period": ("NOTICE_PERIOD", "1 month"),
        "authorized to work": ("WORK_AUTHORIZATION_UK", "Yes"),
        "visa sponsorship": ("SPONSORSHIP_UK", "No"),
    }

    def __init__(self, include_notice_period=True):
        self.include_notice_period=include_notice_period

    def resolve(self, question_text, **_kwargs):
        question=" ".join(str(question_text).lower().split())
        for label,(concept,value) in self._facts.items():
            if label in question:
                if concept == "NOTICE_PERIOD" and not self.include_notice_period:
                    break
                return AnswerDecision(concept,value,"AUTO_FILL","HIGH","SYNTHETIC_TEST_APPROVAL","Synthetic approved test fact.",False,"STANDARD","tests/helpers/synthetic_answer_engine.py")
        return AnswerDecision("UNKNOWN",None,"MANUAL_REVIEW","LOW","MANUAL_REQUIRED","No synthetic approved answer is available.",True,"CONTEXTUAL")

    @staticmethod
    def fit_character_limit(decision, _max_length):
        return decision

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import screening_decision
from app.services.application_service import (
    ApplicationService,
    JobEvaluation,
)


def evaluation_for_score(score):
    decision = SimpleNamespace(
        decision=screening_decision(score),
        overall_score=score,
    )
    return JobEvaluation(
        profile={},
        job_analysis={"company": "Example", "job_title": "Analyst"},
        employer=SimpleNamespace(),
        career_decision=decision,
        ats_result={},
        screening_decision=decision.decision,
    )


@pytest.mark.parametrize("score", [69, 75])
def test_ineligible_evaluations_do_not_generate_documents(score):
    service = ApplicationService.__new__(ApplicationService)

    class MustNotRun:
        def __getattr__(self, name):
            raise AssertionError(f"document stage called: {name}")

    service.resume_strategy_engine = MustNotRun()
    service.resume_composer = MustNotRun()
    service.resume_generator = MustNotRun()
    service.cover_letter_generator = MustNotRun()

    with pytest.raises(ValueError, match="AUTO_APPLY"):
        service.generate_application_documents(evaluation_for_score(score))


def test_auto_apply_evaluation_allows_document_generation(tmp_path, monkeypatch):
    service = ApplicationService.__new__(ApplicationService)
    calls = []

    class FakeStrategy:
        def optimize(self, *args):
            calls.append("strategy")
            return {"summary_focus": []}

    class FakeComposer:
        def compose(self, *args):
            calls.append("compose")
            return {"content": "resume"}

    markdown_path = Path(tmp_path) / "Resume.md"
    markdown_path.write_text("# Resume", encoding="utf-8")

    class FakeGenerator:
        def generate(self, *args):
            calls.append("resume_markdown")
            return str(markdown_path)

    class FakeCoverLetter:
        def generate(self, *args):
            calls.append("cover_letter")
            return "CoverLetter.md", "CoverLetter.docx"

    service.resume_strategy_engine = FakeStrategy()
    service.resume_composer = FakeComposer()
    service.resume_generator = FakeGenerator()
    service.cover_letter_generator = FakeCoverLetter()
    monkeypatch.setattr(
        "app.services.application_service.generate_resume_docx",
        lambda *args, **kwargs: calls.append("resume_docx") or "Resume.docx",
    )

    result = service.generate_application_documents(evaluation_for_score(78))

    assert result.markdown_path == str(markdown_path)
    assert result.docx_path == "Resume.docx"
    assert result.cover_letter_markdown_path == "CoverLetter.md"
    assert result.cover_letter_docx_path == "CoverLetter.docx"
    assert calls == [
        "strategy",
        "compose",
        "resume_markdown",
        "resume_docx",
        "cover_letter",
    ]

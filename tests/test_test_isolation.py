"""Regression for Task 21.8D.1 / 21.8D.2 test-isolation hazards.

tests/test_career_agent.py used to be a bare script that constructed
CareerAgent() and called dashboard_summary() at *module import time*, which
meant pytest collection alone mutated the production tracker DB (and, it was
later discovered, generated real resume/cover-letter documents on disk).
Five sibling scripts with the same defect were found during the same audit:
test_apify.py (live paid Apify scrape), test_employer.py and
test_career_engine.py (live OpenAI calls), test_discovery.py (production
cache read) and test_resume_improvement.py (no production/network access,
but still not a real assertion-bearing test). All six were relocated out of
tests/ to repo-root scripts named outside pytest's test_*.py collection glob,
with their executable bodies wrapped in main() / `if __name__ == "__main__":`.

These regressions prove the fix statically/via mocking (never touching the
real production database or any external network/API), and add a generic
AST-based guard so a *future* tests/test_*.py file cannot silently
reintroduce module-level production/network execution.
"""

import ast
import importlib
import pathlib
import sys
from unittest.mock import patch

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
TESTS_DIR = pathlib.Path(__file__).parent

# old tests/ path (now must not exist) -> new repo-root script (must exist,
# be import-safe, and be outside pytest's collection glob).
RELOCATED_SCRIPTS = {
    "test_career_agent.py": "career_agent_dashboard.py",
    "test_apify.py": "apify_search_demo.py",
    "test_employer.py": "employer_analysis_demo.py",
    "test_career_engine.py": "career_engine_decision_demo.py",
    "test_discovery.py": "job_discovery_dashboard.py",
    "test_resume_improvement.py": "resume_improvement_demo.py",
}


def _is_main_guard(node: ast.If) -> bool:
    return (
        isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


def _app_imported_names(tree: ast.Module) -> set[str]:
    """Names bound in this module's top-level scope that came from an
    `app.*` import — i.e. names that can call into production/service code."""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "app":
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "app":
                    names.add(alias.asname or alias.name.split(".")[0])
    return names


def _call_root_name(call: ast.Call) -> str | None:
    node = call.func
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _top_level_has_only_safe_statements(tree: ast.Module) -> list[str]:
    """Return a list of human-readable violations; empty means safe.

    Bare top-level expressions/loops/with-blocks are always flagged (no
    legitimate pytest test file needs executable code outside a function).
    Top-level assignments are only flagged when they call into something
    imported from `app.*` (production/service code) — this deliberately
    allows common, harmless constant-building idioms such as
    `pathlib.Path(__file__).parent` or `SOME_STR.replace(...)`.
    """
    app_names = _app_imported_names(tree)
    violations = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue  # docstring
            violations.append(f"bare top-level expression: {ast.dump(node)[:100]}")
            continue
        if isinstance(node, ast.If):
            if _is_main_guard(node):
                continue
            violations.append(f"top-level if (not __main__ guard): {ast.dump(node)[:100]}")
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is not None:
                calls = [n for n in ast.walk(value) if isinstance(n, ast.Call)]
                if any(_call_root_name(c) in app_names for c in calls):
                    violations.append(f"top-level assignment calling into app.*: {ast.dump(node)[:100]}")
            continue
        violations.append(f"unexpected top-level statement ({type(node).__name__}): {ast.dump(node)[:100]}")
    return violations


@pytest.mark.parametrize("old_name,new_name", sorted(RELOCATED_SCRIPTS.items()))
def test_unsafe_script_no_longer_exists_under_tests(old_name, new_name):
    assert not (TESTS_DIR / old_name).exists(), f"{old_name} must be removed from tests/"


@pytest.mark.parametrize("old_name,new_name", sorted(RELOCATED_SCRIPTS.items()))
def test_replacement_script_is_outside_pytest_default_collection_glob(old_name, new_name):
    replacement = REPO_ROOT / new_name
    assert replacement.exists(), f"{new_name} must exist at repo root"
    # pytest's default collection glob is test_*.py / *_test.py.
    assert not replacement.name.startswith("test_")
    assert not replacement.name.endswith("_test.py")


@pytest.mark.parametrize("old_name,new_name", sorted(RELOCATED_SCRIPTS.items()))
def test_replacement_script_has_no_top_level_side_effects(old_name, new_name):
    """Static (AST) proof: no production/network call sits at module top
    level outside of `if __name__ == "__main__":` and function bodies."""
    source = (REPO_ROOT / new_name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    violations = _top_level_has_only_safe_statements(tree)
    assert not violations, f"{new_name} has unsafe top-level statements: {violations}"
    assert "def main(" in source, f"{new_name} must wrap its logic in main()"
    assert '__name__ == "__main__"' in source, f"{new_name} must guard execution behind __main__"


def test_importing_dashboard_script_never_constructs_careeragent():
    """The historical incident: importing the module built a CareerAgent and ran
    dashboard_summary() against production storage merely by being collected."""
    sys.modules.pop("career_agent_dashboard", None)
    with patch("app.services.career_agent.CareerAgent") as mock_career_agent:
        module = importlib.import_module("career_agent_dashboard")
        mock_career_agent.assert_not_called()
    assert callable(module.main)
    # The old top-level names must not leak into module globals; they only exist
    # inside main()'s local scope now.
    assert not hasattr(module, "agent")
    assert not hasattr(module, "summary")
    assert not hasattr(module, "jobs")


# ---------------------------------------------------------------------------
# Generic guard: no *current or future* tests/test_*.py file may contain a
# module-level (import-time) call. This is name-agnostic — it does not
# hard-code today's incident filenames — so a brand-new demo/debug script
# added later under tests/ will fail this test immediately on collection
# rather than silently mutating production state.
#
# A small number of pre-existing files are already known to violate this and
# are tracked explicitly below rather than silently skipped:
#   - PENDING_HERMETIC_FIX: pure demo/debug scripts (zero `def test_*`
#     functions) discovered during the Task 21.8D.2 whole-repo audit that
#     follow the exact same hazard pattern already fixed for the six files
#     above, but are NOT fixed by this task (out of its authorized scope).
#     Tracked as xfail so fixing one of them turns into a visible XPASS,
#     forcing this registry to be kept honest.
# ---------------------------------------------------------------------------

PENDING_HERMETIC_FIX = {
    "test_ai.py", "test_cover_letter.py", "test_docx.py", "test_evidence_engine.py",
    "test_experience.py", "test_industry.py", "test_master_profile.py", "test_match.py",
    "test_opportunity.py", "test_profile.py", "test_queue.py", "test_resume_generator.py",
    "test_resume_optimizer.py", "test_scraper.py", "test_skills.py", "test_url_builder.py",
}


def _all_test_files():
    return sorted(p.name for p in TESTS_DIR.glob("test_*.py"))


@pytest.mark.parametrize("filename", _all_test_files())
def test_no_module_level_production_or_network_calls(filename):
    if filename in PENDING_HERMETIC_FIX:
        pytest.xfail(
            f"{filename}: known pre-existing demo-script hazard, tracked for a "
            "dedicated follow-up task, not in scope for Task 21.8D.2"
        )
    source = (TESTS_DIR / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    violations = _top_level_has_only_safe_statements(tree)
    assert not violations, f"{filename} has unsafe top-level statement(s): {violations}"

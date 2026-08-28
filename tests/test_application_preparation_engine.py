from app.services.application_preparation_engine import ApplicationPreparationEngine
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_answer_vault import ApplicationAnswerVault


def _isolated_engine(tmp_path, **kwargs):
    # A real (non-synthetic) answer engine is required here: these tests exercise
    # the production concept-matching/market-rule behavior, not canned synthetic
    # answers. It is backed by a fresh, isolated vault (tmp_path) rather than the
    # production app/data/application_answer_vault.json -- a brand-new vault file
    # seeds identically to production's original seed data, so behavior is
    # unchanged while no production file is ever read or written.
    vault = ApplicationAnswerVault(tmp_path / "vault.json")
    browser = ApplicationBrowserService(preview_folder=tmp_path / "previews", answer_engine=ApplicationAnswerEngine(vault))
    return ApplicationPreparationEngine(browser_service=browser, session_dir=tmp_path, **kwargs)

P1='''<form class="greenhouse"><label for="email">Email</label><input id="email" type="email" required><label for="phone">Phone</label><input id="phone" type="tel" required><button>Next</button></form>'''
P2='''<form class="greenhouse"><label for="s">Will you require visa sponsorship?</label><select id="s" required><option>Yes</option><option>No</option></select><label for="a">Are you authorized to work in the UK?</label><select id="a" required><option>Yes</option><option>No</option></select><button>Continue</button></form>'''
P3='''<form class="greenhouse"><label for="cv">Resume / CV</label><input id="cv" type="file" required><label for="unknown">Describe your most unusual achievement</label><input id="unknown" required><label for="legal">I certify the information is accurate</label><input id="legal" type="checkbox" required><button>Save and Continue</button></form>'''
REVIEW='<html><h1>Review your application</h1><button>Submit Application</button></html>'

def vacancy(**extra):
    return {"application_url":"https://boards.greenhouse.io/example/jobs/1","application_portal":"GREENHOUSE","application_route_confidence":"HIGH","market":"united_kingdom",**extra}

def test_multi_page_preparation_batches_required_exceptions(tmp_path):
    engine=_isolated_engine(tmp_path)
    session=engine.prepare_pages([{"html":P1,"url":"https://boards.greenhouse.io/x/1"},{"html":P2,"url":"https://boards.greenhouse.io/x/2"},{"html":P3,"url":"https://boards.greenhouse.io/x/3"}],vacancy(),application_date="2026-09-10")
    assert session.state=="MANUAL_INPUT_REQUIRED" and session.pages_processed==3
    assert session.fields_filled==4
    assert {e.exception_type for e in session.exceptions}>={"DOCUMENT_NOT_READY","LEGAL_DECLARATION","MANUAL_REQUIRED"}
    assert all(event["action_type"]!="FINAL_SUBMIT_CLICKED" for event in session.audit)

def test_optional_unknown_does_not_block_and_review_stops_before_submit(tmp_path):
    engine=_isolated_engine(tmp_path)
    optional='<form><label for="x">Unknown optional</label><input id="x"><button>Next Step</button></form>'
    session=engine.prepare_pages([optional,REVIEW],vacancy())
    assert session.state=="READY_FOR_FINAL_REVIEW" and session.fields_skipped==1 and not session.final_review_detected is False

def test_loop_and_navigation_bounds_stop_safely(tmp_path):
    engine=_isolated_engine(tmp_path)
    loop='<form><label for="x">Email</label><input id="x" type="email"><button>Next</button></form>'
    assert engine.prepare_pages([loop,loop],vacancy()).failure_reason=="LOOP_DETECTED"
    assert engine.prepare_pages([{"html":loop,"url":"https://a/1"},{"html":loop,"url":"https://a/2"}],vacancy(),max_navigation_actions=1).failure_reason=="MAX_NAVIGATION_ACTIONS_EXCEEDED"

def test_boundaries_and_unexpected_success(tmp_path):
    engine=_isolated_engine(tmp_path)
    for html,state in (("<form>Log in</form>","AUTH_REQUIRED"),("<form>reCAPTCHA</form>","CAPTCHA_REQUIRED"),("<form>verification code MFA</form>","MFA_REQUIRED"),("<form>Create an account</form>","ACCOUNT_CREATION_REQUIRED")):
        assert engine.prepare_pages([html],vacancy()).state==state
    assert engine.prepare_pages(["<html>Application submitted successfully</html>"],vacancy()).failure_reason=="UNEXPECTED_APPLICATION_SUCCESS"

def test_session_persistence_contains_no_secrets(tmp_path):
    engine=_isolated_engine(tmp_path); session=engine.prepare_pages([P3],vacancy())
    text=(tmp_path/f"{session.session_id}.json").read_text().lower()
    assert all(term not in text for term in ("password","cookie","csrf","otp","authorization header"))
    assert engine.load(session.session_id).session_id==session.session_id

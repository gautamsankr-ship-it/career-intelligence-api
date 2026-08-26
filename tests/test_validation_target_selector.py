from app.services.validation_target_selector import ValidationTargetSelector


class Snapshot:
    def __init__(self, records): self.records=records
    def load(self): return self.records


class Probe:
    async def validate_url(self, url, market, **kwargs):
        state="CAPTCHA_REQUIRED" if "captcha" in url else "INSPECTED"
        return {"portal":"GREENHOUSE" if "green" in url else "LEVER", "state":state,
                "fields_detected":0 if state == "CAPTCHA_REQUIRED" else 12,
                "application_surface":"MAIN_DOCUMENT", "fields_filled":0, "documents_uploaded":0,
                "navigation_actions":[], "tracker_id":None, "application_submitted":False}


def record(company, portal, url, confidence="HIGH", ready=True):
    return {"company":company,"job_title":"Finance Manager","market":"united_kingdom","application_portal":portal,
            "application_url":url,"application_url_type":"ATS_URL","route_confidence":confidence,"browser_ready":ready}


def selector(tmp_path, records): return ValidationTargetSelector(Snapshot(records), tmp_path / "probes.json", Probe())


def test_only_greenhouse_lever_high_quality_routes_are_candidates(tmp_path):
    target=selector(tmp_path, [record("Green", "GREENHOUSE", "https://green.example/1"), record("Lever", "LEVER", "https://lever.example/1"), record("Workday", "WORKDAY", "https://workday.example/1"), record("Bad", "GREENHOUSE", "", ready=False)])
    assert [x["application_portal"] for x in target.candidates()] == ["GREENHOUSE", "LEVER"]


def test_accessible_target_ranks_above_captcha(tmp_path):
    target=selector(tmp_path, [record("Captcha", "GREENHOUSE", "https://green.example/captcha"), record("Accessible", "LEVER", "https://lever.example/ok")])
    results=target.probe(limit=5)
    assert results[0]["probe_state"] == "FORM_ACCESSIBLE"
    assert results[1]["probe_state"] == "CAPTCHA_REQUIRED"
    assert results[0]["fields_detected"] == 12


def test_probe_is_inspection_only_and_has_no_tracker_side_effect(tmp_path):
    target=selector(tmp_path, [record("Accessible", "GREENHOUSE", "https://green.example/ok")])
    result=target.probe(limit=1)[0]
    assert result["probe_state"] == "FORM_ACCESSIBLE"
    saved=target.results()[result["application_url"]]
    assert saved["fields_detected"] == 12 and saved["surface"] == "MAIN_DOCUMENT"


def test_filters_and_probe_result_persistence(tmp_path):
    target=selector(tmp_path, [record("Acme", "GREENHOUSE", "https://green.example/1"), record("Other", "LEVER", "https://lever.example/2")])
    assert len(target.ranked(portal="GREENHOUSE")) == 1
    assert len(target.ranked(company="other")) == 1
    target.probe(limit=1, portal="GREENHOUSE")
    assert target.results()


class SessionProbe:
    def __init__(self, session=None, error=None): self.session=session or {}; self.error=error; self.calls=0
    async def validate_url(self, url, market, **kwargs):
        self.calls += 1
        if self.error: raise self.error
        return self.session


def test_databricks_like_captcha_preserves_greenhouse_iframe_and_stable_url(tmp_path):
    url="https://databricks.com/company/careers/open-positions/job?gh_jid=8604614002"
    probe=SessionProbe({"portal":"GREENHOUSE", "state":"CAPTCHA_REQUIRED", "fields_detected":0,
                        "application_surface":"IFRAME", "wrapper_detected":True,
                        "final_url":"https://boards.greenhouse.io/embed/job_app?token=secret"})
    target=ValidationTargetSelector(Snapshot([record("Databricks", "GREENHOUSE", url)]), tmp_path / "probes.json", probe)
    saved=target.probe(limit=1)[0]
    result=target.results()[url]
    assert (saved["application_portal"], result["probe_state"], result["surface"], result["fields_detected"]) == ("GREENHOUSE", "CAPTCHA_REQUIRED", "IFRAME", 0)
    assert result["application_url"] == url and "token" not in str(result)


def test_accessible_greenhouse_and_lever_map_to_form_accessible(tmp_path):
    for portal in ("GREENHOUSE", "LEVER"):
        target=ValidationTargetSelector(Snapshot([record(portal, portal, f"https://{portal.lower()}.example/1")]), tmp_path / f"{portal}.json", SessionProbe({"portal":portal, "state":"INSPECTED", "fields_detected":12, "application_surface":"MAIN_DOCUMENT"}))
        assert target.probe(limit=1)[0]["probe_state"] == "FORM_ACCESSIBLE"


def test_known_blockers_and_wrapper_not_found_remain_distinct(tmp_path):
    for state, expected in (("AUTH_REQUIRED", "AUTH_REQUIRED"), ("MFA_REQUIRED", "MFA_REQUIRED"), ("ACCOUNT_CREATION_REQUIRED", "ACCOUNT_CREATION_REQUIRED"), ("GREENHOUSE_WRAPPER_FORM_NOT_FOUND", "FORM_NOT_FOUND")):
        target=ValidationTargetSelector(Snapshot([record("Green", "GREENHOUSE", f"https://green.example/{state}")]), tmp_path / f"{state}.json", SessionProbe({"portal":"GREENHOUSE", "state":state, "fields_detected":0, "application_surface":"IFRAME"}))
        result=target.probe(limit=1)[0]
        assert result["probe_state"] == expected and result["application_portal"] == "GREENHOUSE"


def test_genuine_invocation_exception_is_browser_error_and_refresh_replaces_stale_result(tmp_path):
    url="https://green.example/1"
    probe=SessionProbe(error=RuntimeError("browser unavailable"))
    target=ValidationTargetSelector(Snapshot([record("Green", "GREENHOUSE", url)]), tmp_path / "probes.json", probe)
    assert target.probe(limit=1)[0]["probe_state"] == "BROWSER_ERROR"
    probe.error=None; probe.session={"portal":"GREENHOUSE", "state":"CAPTCHA_REQUIRED", "fields_detected":0, "application_surface":"IFRAME"}
    assert target.probe(limit=1, refresh=True)[0]["probe_state"] == "CAPTCHA_REQUIRED"
    assert probe.calls == 2

from app.models.career_opportunity import CareerOpportunity
from app.services.discovery_route_snapshot import DiscoveryRouteSnapshotService
from app.services.job_discovery_service import JobDiscoveryService


def job(portal="GREENHOUSE", url="https://boards.greenhouse.io/example/jobs/1", work="UNKNOWN"):
    return CareerOpportunity(company="Databricks", job_title="Finance Systems Manager", market="united_kingdom",
        source="EmployerCareerSite", job_url=url, source_listing_url=url, application_url=url,
        application_url_type="ATS_URL", application_portal=portal, application_route_confidence="HIGH",
        work_arrangement=work, metadata={"career_track":"BOTH"})


def test_direct_route_is_snapshotted_before_remote_gate_and_unknown_stays_excluded(tmp_path):
    snapshot=DiscoveryRouteSnapshotService(tmp_path / "routes.json")
    candidate=job()
    records=snapshot.save_routes([candidate])
    assert records[0]["application_portal"] == "GREENHOUSE"
    assert records[0]["browser_ready"] is True
    assert JobDiscoveryService().filter_remote_jobs([candidate]) == []


def test_greenhouse_and_lever_routes_survive_and_deduplicate(tmp_path):
    snapshot=DiscoveryRouteSnapshotService(tmp_path / "routes.json")
    greenhouse=job(); lever=job("LEVER", "https://jobs.lever.co/example/abc", "HYBRID")
    assert len(snapshot.save_routes([greenhouse, lever])) == 2
    assert len(snapshot.save_routes([greenhouse, lever])) == 2
    assert {x["application_portal"] for x in snapshot.load()} == {"GREENHOUSE", "LEVER"}


def test_snapshot_retention_is_bounded(tmp_path):
    snapshot=DiscoveryRouteSnapshotService(tmp_path / "routes.json", retention_limit=2)
    routes=[job("GREENHOUSE", f"https://boards.greenhouse.io/example/jobs/{index}") for index in range(3)]
    assert len(snapshot.save_routes(routes)) == 2


def test_snapshot_has_no_tracker_or_decision_fields(tmp_path):
    record=DiscoveryRouteSnapshotService(tmp_path / "routes.json").save_routes([job()])[0]
    assert "tracker_id" not in record and "decision" not in record and "application_history" not in record

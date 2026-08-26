from app.models.career_opportunity import CareerOpportunity
from app.services.job_discovery_service import JobDiscoveryService
from app.services.job_sources import IndeedSourceAdapter, JobSourceAdapter, MultiSourceJobDiscovery, normalize_job_item


class FakeActor:
    def __init__(self, run):
        self.run = run
        self.inputs = []

    def call(self, run_input):
        self.inputs.append(run_input)
        return self.run


class FakeDataset:
    def __init__(self, items):
        self.items = items

    def iterate_items(self):
        return iter(self.items)


class FakeClient:
    def __init__(self, run, items=(), log_text=""):
        self.actor_client = FakeActor(run)
        self.dataset_client = FakeDataset(items)
        self.log_text = log_text

    def actor(self, actor_id):
        return self.actor_client

    def dataset(self, dataset_id):
        return self.dataset_client

    def log(self, run_id):
        class Log:
            def __init__(self, text):
                self.text = text

            def get(self):
                return self.text
        return Log(self.log_text)


def linkedin(description="", **fields):
    return normalize_job_item({"title": "Finance Manager", "descriptionText": description, **fields}, "LinkedIn")


def test_linkedin_work_arrangement_evidence_precedence_and_strict_retention():
    structured = linkedin(workplaceType="Remote")
    hybrid = linkedin("This is a hybrid working role with 2 days from home.")
    onsite = linkedin("This is an on-site role. Remote working is not available.")
    explicit = linkedin("A fully remote position in a distributed team.")
    technical = linkedin("Support remote systems for global clients.")
    discovery = JobDiscoveryService()

    assert structured.work_arrangement == "REMOTE"
    assert hybrid.work_arrangement == "HYBRID"
    assert onsite.work_arrangement == "ON_SITE"
    assert explicit.work_arrangement == "REMOTE"
    assert technical.work_arrangement == "UNKNOWN"
    assert discovery.filter_remote_jobs([structured, hybrid, onsite, explicit, technical]) == [structured, explicit]


def test_listing_level_remote_title_is_accepted_but_remote_search_context_cannot_override_hybrid():
    title_remote = normalize_job_item({"title": "Finance Manager (Remote)"}, "LinkedIn")
    hybrid = normalize_job_item(
        {"title": "Finance Manager (Remote)", "descriptionText": "Hybrid working with 2 days from home.", "inputUrl": "https://linkedin.example/?f_WT=2"},
        "LinkedIn",
    )
    search_only = normalize_job_item({"title": "Finance Manager", "inputUrl": "https://linkedin.example/?f_WT=2"}, "LinkedIn")

    assert title_remote.work_arrangement == "REMOTE"
    assert hybrid.work_arrangement == "HYBRID"
    assert search_only.work_arrangement == "UNKNOWN"


def test_remote_scope_is_not_claimed_for_unknown_or_non_remote_jobs():
    unknown = linkedin("Remote systems support")
    remote_global = linkedin("Work from anywhere in a fully remote role.")
    remote_restricted = linkedin("Remote role. Candidates must reside in Australia.")

    assert unknown.remote_scope == "REMOTE_NOT_APPLICABLE"
    assert remote_global.remote_scope == "REMOTE_GLOBAL"
    assert remote_restricted.remote_scope == "REMOTE_COUNTRY_RESTRICTED"


def test_linkedin_search_provenance_does_not_make_unknown_remote():
    job = CareerOpportunity(source="LinkedIn", work_arrangement="UNKNOWN", metadata={"requested_work_arrangement": "REMOTE", "remote_search": True})
    assert JobDiscoveryService().work_arrangement(job) == "UNKNOWN"


def test_indeed_successful_zero_results_is_not_failure():
    adapter = IndeedSourceAdapter(client=FakeClient({"status": "SUCCEEDED", "defaultDatasetId": "d"}), rotation_index=0)
    result = MultiSourceJobDiscovery({"indeed": adapter}).discover(("indeed",), 3)
    assert result.source_counts == {"indeed": 0}
    assert result.failures == {}


def test_indeed_running_actor_limit_is_source_failure_and_skips_repeated_calls():
    client = FakeClient({"id": "run-1", "status": "RUNNING", "defaultDatasetId": "d"}, log_text="Free-plan limit reached for this Actor. Upgrade your Apify plan to continue.")
    adapter = IndeedSourceAdapter(client=client, rotation_index=0)
    result = MultiSourceJobDiscovery({"indeed": adapter}).discover(("indeed",), 3)

    assert len(client.actor_client.inputs) == 1
    assert result.failures == {"indeed": "ACTOR_LIMIT: free-plan/usage limit reached"}


def test_indeed_actor_limit_does_not_discard_successful_linkedin_results():
    class LinkedInSuccess(JobSourceAdapter):
        source_name = "linkedin"

        def discover(self, count):
            return [CareerOpportunity(source="LinkedIn", market="united_kingdom", id="uk-1")]

    client = FakeClient({"id": "run-1", "status": "RUNNING", "defaultDatasetId": "d"}, log_text="Usage limit reached")
    result = MultiSourceJobDiscovery({"linkedin": LinkedInSuccess(), "indeed": IndeedSourceAdapter(client=client, rotation_index=0)}).discover(("linkedin", "indeed"), 3)

    assert [(job.source, job.market) for job in result.jobs] == [("LinkedIn", "united_kingdom")]
    assert result.failures["indeed"].startswith("ACTOR_LIMIT")

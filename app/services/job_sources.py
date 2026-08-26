"""Source adapters that normalize public job records into CareerOpportunity."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

from apify_client import ApifyClient

from app.config import (
    HAYS_APIFY_ACTOR_ID,
    HAYS_SEARCH_URLS,
    INDEED_APIFY_ACTOR_ID,
    SEEK_APIFY_ACTOR_ID,
    SEEK_SEARCH_URLS,
    ROBERT_HALF_APIFY_ACTOR_ID,
    ROBERT_HALF_SEARCH_URLS,
)
from app.models.career_opportunity import CareerOpportunity
from app.services.apify_service import ApifyJobService
from app.services.job_search_config import DISCOVERY_QUERY_CYCLE, TARGET_MARKETS, indeed_searches, linkedin_market_searches
from app.services.linkedin_url_builder import LinkedInURLBuilder
from app.services.application_route_resolver import ApplicationRouteResolver


class SourceUnavailableError(RuntimeError):
    """Raised when an optional source has not been configured safely."""


class ApifyRunFailedError(RuntimeError):
    """Raised when an Apify actor completes without a successful status."""


class ApifyActorLimitError(RuntimeError):
    """Raised when an actor reports an account or plan limit despite success."""


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return ""


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(_first(value, "raw", "name", "displayName", "label", "text"))
    return "" if value is None else str(value)


def _work_arrangement_evidence(item: dict[str, Any]) -> tuple[str, str]:
    """Return conservative arrangement and concise listing-level evidence."""
    work_mode = _text(_first(item, "work_mode", "workMode", "workplace_type", "workplaceType", "workArrangement")).lower()
    if work_mode in {"remote", "fully remote", "work from home"}:
        return "REMOTE", f"structured work arrangement: {work_mode}"
    if work_mode in {"hybrid", "flexible hybrid"}:
        return "HYBRID", f"structured work arrangement: {work_mode}"
    if work_mode in {"onsite", "on-site", "on site", "office"}:
        return "ON_SITE", f"structured work arrangement: {work_mode}"

    value = _first(item, "is_remote", "remote", "Remote", "isRemote", "workFromHome", "remoteStatus")
    if isinstance(value, bool):
        return ("REMOTE", "structured remote flag: true") if value else ("ON_SITE", "structured remote flag: false")
    if isinstance(value, str) and value.lower() in {"true", "remote", "yes"}:
        return "REMOTE", f"structured remote flag: {value.lower()}"
    if isinstance(value, str) and value.lower() in {"false", "onsite", "on-site", "no"}:
        return "ON_SITE", f"structured remote flag: {value.lower()}"

    title = _text(_first(item, "title", "jobTitle", "positionName")).casefold()
    description = _text(_first(item, "descriptionText", "description", "jobDescription", "Description")).casefold()
    location = _text(_first(item, "location", "jobLocation", "locationName", "Location")).casefold().strip()
    text = f"{title} {description}"
    # Explicit contradictory wording wins over remote wording.
    if any(term in text for term in ("hybrid working", "hybrid role", "hybrid work", "days in office", "days from home")):
        return "HYBRID", "vacancy wording: hybrid"
    if any(term in text for term in ("on-site role", "onsite role", "office-based", "must work from our office", "five days in office", "remote working is not available")):
        return "ON_SITE", "vacancy wording: on-site"
    if location in {"remote", "remote - worldwide", "worldwide remote"}:
        return "REMOTE", "listing location: remote"
    if "(remote)" in title or "[remote]" in title:
        return "REMOTE", "vacancy title: remote"
    if any(term in text for term in ("fully remote", "100% remote", "remote position", "remote role", "this role is remote", "this position is remote", "work remotely", "work from anywhere", "work from home", "home-based", "distributed team", "remote-first")):
        return "REMOTE", "vacancy wording: explicit remote"
    return "UNKNOWN", "no reliable workplace evidence"


def _work_arrangement(item: dict[str, Any]) -> str:
    return _work_arrangement_evidence(item)[0]


def _remote_status(item: dict[str, Any]) -> bool | None:
    arrangement = _work_arrangement(item)
    if arrangement == "REMOTE":
        return True
    if arrangement == "ON_SITE":
        return False
    return None


def _work_arrangement_source(item: dict[str, Any]) -> str:
    """Record which explicit source field supplied the arrangement."""
    if _first(item, "work_mode", "workMode", "workplace_type", "workplaceType", "workArrangement"):
        return "work_mode"
    if _first(item, "is_remote", "remote", "Remote", "isRemote", "workFromHome", "remoteStatus") != "":
        return "is_remote"
    return ""


def _remote_scope(item: dict[str, Any], arrangement: str) -> str:
    """Keep a small, diagnostic-only signal about stated remote restrictions."""
    if arrangement != "REMOTE":
        return "REMOTE_NOT_APPLICABLE"
    restriction = " ".join(
        _text(_first(item, key))
        for key in ("work_authorization", "workAuthorization", "right_to_work", "residency_requirements", "location_restrictions", "remote_scope")
    ).lower()
    description = _text(_first(item, "descriptionText", "description", "jobDescription", "Description")).lower()
    text = f"{restriction} {description}"
    if any(term in text for term in ("work from anywhere", "worldwide", "global remote")):
        return "REMOTE_GLOBAL"
    if any(term in text for term in ("right to work", "work authorization", "eligible to work", "must reside", "must live", "within australia", "within the uk", "within uk", "within the us", "within us", "us remote", "uk remote", "specific state")):
        return "REMOTE_COUNTRY_RESTRICTED"
    return "REMOTE_UNCLEAR"


def normalize_job_item(item: dict[str, Any], source: str) -> CareerOpportunity:
    """Normalize common Apify/public job fields without inventing values."""
    metadata = dict(item)
    arrangement, arrangement_evidence = _work_arrangement_evidence(item)
    evidence_source = _work_arrangement_source(item)
    if evidence_source:
        metadata["work_arrangement_source"] = evidence_source
        metadata["work_arrangement_raw"] = _text(_first(item, "work_mode", "workMode", "workplace_type", "workplaceType", "workArrangement"))
    metadata["work_arrangement_evidence"] = arrangement_evidence
    source_listing_url = _text(_first(item, "link", "url", "jobUrl", "job_url", "platform_url", "Job Detail URL"))
    raw_route_field = next((key for key in ("externalApplyLink", "applicationUrl", "application_url", "applyUrl", "apply_url", "companyApplyUrl", "jobApplyUrl", "applyLink", "externalUrl", "redirectUrl") if item.get(key) not in (None, "")), "")
    raw_application_url = _text(item.get(raw_route_field)) if raw_route_field else ""
    resolver = ApplicationRouteResolver()
    route = resolver.resolve({"job_url": source_listing_url, "application_url": raw_application_url})
    if raw_application_url and route.resolution_status == "RESOLVED":
        metadata["application_url_source_field"] = raw_route_field
    metadata["application_route_status"] = route.resolution_status
    return CareerOpportunity(
        id=_text(_first(item, "id", "jobId", "job_id", "positionId", "jobKey", "Unique Job Number", "unique_job_number")),
        source=source,
        job_title=_text(_first(item, "title", "jobTitle", "positionName", "position", "name", "Job Title")),
        company=_text(_first(item, "companyName", "company", "company_name", "employer", "Company", "Source")),
        location=_text(_first(item, "location", "jobLocation", "locationName", "Location")),
        employment_type=_text(_first(item, "employmentType", "jobType", "workType", "Employment Type")),
        salary=_text(_first(item, "salary", "salaryText", "salaryDisplay")),
        posted_date=_text(_first(item, "postedAt", "postingDateParsed", "posted_date", "datePosted", "Date Posted")),
        job_description=_text(_first(item, "descriptionText", "description", "jobDescription", "descriptionHtml", "Description")),
        job_url=source_listing_url,
        source_listing_url=source_listing_url,
        application_url=route.application_url,
        application_url_type=route.application_url_type,
        application_url_source=f"DISCOVERY_METADATA:{raw_route_field}" if raw_route_field else "",
        application_portal=route.portal,
        application_route_confidence=route.route_confidence,
        application_route_resolved_at=route.resolved_at,
        application_route_status=route.resolution_status if route.resolution_status != "EXTERNAL_ROUTE_UNRESOLVED" else "SOURCE_ONLY",
        remote_status=_remote_status(item),
        work_arrangement=arrangement,
        remote_scope=_remote_scope(item, arrangement),
        metadata=metadata,
    )


class JobSourceAdapter:
    source_name: str
    market_failures: dict[str, str] = {}

    def discover(self, count: int) -> list[CareerOpportunity]:
        raise NotImplementedError


@dataclass(frozen=True)
class MultiSourceDiscoveryResult:
    jobs: list[CareerOpportunity]
    source_counts: dict[str, int]
    failures: dict[str, str]


class MultiSourceJobDiscovery:
    """Runs enabled source adapters independently so one outage is isolated."""

    def __init__(self, adapters: dict[str, JobSourceAdapter] | None = None) -> None:
        self.adapters = adapters or default_source_adapters()

    def discover(self, sources: Iterable[str], count: int) -> MultiSourceDiscoveryResult:
        jobs: list[CareerOpportunity] = []
        source_counts: dict[str, int] = {}
        failures: dict[str, str] = {}
        for source in sources:
            adapter = self.adapters.get(source.lower())
            if adapter is None:
                failures[source] = "Unknown job source."
                continue
            try:
                discovered = adapter.discover(count)
                source_counts[source] = len(discovered)
                jobs.extend(discovered)
                if getattr(adapter, "market_failures", {}).get("__source__"):
                    failures[source] = adapter.market_failures["__source__"]
                    continue
                for market, error in getattr(adapter, "market_failures", {}).items():
                    failures[f"{source}/{market}"] = error
            except Exception as exc:
                source_counts[source] = 0
                failures[source] = str(exc)
        return MultiSourceDiscoveryResult(jobs, source_counts, failures)


class LinkedInSourceAdapter(JobSourceAdapter):
    source_name = "linkedin"

    def __init__(self, builder=None, scraper=None, rotation_index: int | None = None, rotation_state_path: Path | None = None) -> None:
        self.builder = builder or LinkedInURLBuilder()
        self.scraper = scraper or ApifyJobService()
        self.rotation_index = rotation_index
        self.rotation_state_path = rotation_state_path or Path("app/data/cache/linkedin_family_rotation.json")

    def _next_rotation_index(self) -> int:
        if self.rotation_index is not None:
            return self.rotation_index % len(DISCOVERY_QUERY_CYCLE)
        try:
            state = json.loads(self.rotation_state_path.read_text(encoding="utf-8"))
            return int(state.get("next_index", date.today().toordinal())) % len(DISCOVERY_QUERY_CYCLE)
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            return date.today().toordinal() % len(DISCOVERY_QUERY_CYCLE)

    def _advance_rotation(self, rotation_index: int) -> None:
        if self.rotation_index is not None:
            return
        self.rotation_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.rotation_state_path.write_text(
            json.dumps({"next_index": (rotation_index + 1) % len(DISCOVERY_QUERY_CYCLE)}),
            encoding="utf-8",
        )

    def discover(self, count: int) -> list[CareerOpportunity]:
        jobs: list[CareerOpportunity] = []
        self.market_failures = {}
        rotation_index = self._next_rotation_index()
        for market_index, market in enumerate(TARGET_MARKETS):
            market_searches = linkedin_market_searches(market, count, rotation_index + market_index)
            try:
                discovered = self.scraper.scrape_jobs(
                    [search["url"] for search in self.builder.build_urls(market_searches)], count=count
                )
            except Exception as exc:
                self.market_failures[market.key] = str(exc)
                continue
            for job in discovered:
                raw = job.metadata if isinstance(job.metadata, dict) else {}
                # This is assigned from the actor run's input, not inferred from
                # the listing location. ``inputUrl`` remains in raw metadata.
                job.market = market.key
                arrangement = _work_arrangement(raw)
                if arrangement != "UNKNOWN":
                    job.work_arrangement = arrangement
                    job.remote_status = True if arrangement == "REMOTE" else False if arrangement == "ON_SITE" else None
                job.remote_scope = _remote_scope(raw, job.work_arrangement)
                job.metadata["market"] = job.market
                job.metadata["requested_work_arrangement"] = "REMOTE"
                job.metadata["remote_search"] = True
            jobs.extend(discovered)
        self._advance_rotation(rotation_index)
        return jobs


class ApifyJobSourceAdapter(JobSourceAdapter):
    """Small reusable adapter for actors returning job records in a dataset."""

    def __init__(
        self,
        source_name: str,
        actor_id: str,
        input_factory: Callable[[int], dict[str, Any]],
        client=None,
    ) -> None:
        self.source_name = source_name
        self.actor_id = actor_id
        self.input_factory = input_factory
        self.client = client or ApifyClient(os.getenv("APIFY_TOKEN"))

    def discover(self, count: int) -> list[CareerOpportunity]:
        if not self.actor_id:
            raise SourceUnavailableError(
                f"{self.source_name} discovery is disabled: no approved Apify actor is configured."
            )
        run = self.client.actor(self.actor_id).call(run_input=self.input_factory(count))
        status = _run_value(run, "status")
        if status not in {"SUCCEEDED", "SUCCESS"}:
            rendered_status = status or "missing"
            raise ApifyRunFailedError(
                f"{self.source_name} Apify actor '{self.actor_id}' completed with status {rendered_status}."
            )
        dataset_id = _run_value(run, "default_dataset_id", "defaultDatasetId")
        if not dataset_id:
            raise RuntimeError(f"{self.source_name} actor did not return a dataset ID.")
        return [
            normalize_job_item(item, _source_label(self.source_name))
            for item in self.client.dataset(dataset_id).iterate_items()
        ]


def _run_value(run: Any, *names: str) -> Any:
    for name in names:
        value = getattr(run, name, None)
        if value not in (None, ""):
            return value
        if isinstance(run, dict):
            value = run.get(name)
            if value not in (None, ""):
                return value
    return None


def _actor_limit_message(run: Any, client=None) -> str:
    """Return a concise plan-limit signal from run metadata or its short log."""
    text = " ".join(str(_run_value(run, name) or "") for name in (
        "status_message", "statusMessage", "error_message", "errorMessage", "message"
    ))
    run_id = _run_value(run, "id")
    if client is not None and run_id and hasattr(client, "log"):
        try:
            text = f"{text} {client.log(run_id).get() or ''}"
        except Exception:
            pass
    lowered = text.casefold()
    patterns = ("free-plan limit", "free plan limit", "usage limit", "quota exceeded", "upgrade your plan", "actor limit", "payment required", "insufficient credits")
    if any(pattern in lowered for pattern in patterns):
        return "ACTOR_LIMIT: free-plan/usage limit reached"
    return ""


def _source_label(source_name: str) -> str:
    return {
        "linkedin": "LinkedIn",
        "indeed": "Indeed",
        "seek": "Seek",
        "hays": "Hays",
        "robert_half": "Robert Half",
    }.get(source_name, source_name.title())


class IndeedSourceAdapter(ApifyJobSourceAdapter):
    def __init__(
        self,
        client=None,
        rotation_index: int | None = None,
        rotation_state_path: Path | None = None,
    ) -> None:
        super().__init__(
            "indeed",
            INDEED_APIFY_ACTOR_ID,
            lambda count: {"max_results": count},
            client,
        )
        self.rotation_index = rotation_index
        self.rotation_state_path = rotation_state_path or Path("app/data/cache/indeed_family_rotation.json")

    def _next_rotation_index(self) -> int:
        if self.rotation_index is not None:
            return self.rotation_index % len(DISCOVERY_QUERY_CYCLE)
        try:
            state = json.loads(self.rotation_state_path.read_text(encoding="utf-8"))
            return int(state.get("next_index", date.today().toordinal())) % len(DISCOVERY_QUERY_CYCLE)
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            return date.today().toordinal() % len(DISCOVERY_QUERY_CYCLE)

    def _advance_rotation(self, rotation_index: int) -> None:
        if self.rotation_index is not None:
            return
        self.rotation_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.rotation_state_path.write_text(
            json.dumps({"next_index": (rotation_index + 1) % len(DISCOVERY_QUERY_CYCLE)}),
            encoding="utf-8",
        )

    def discover(self, count: int) -> list[CareerOpportunity]:
        if not self.actor_id:
            raise SourceUnavailableError("indeed discovery is disabled: no approved Apify actor is configured.")
        jobs: list[CareerOpportunity] = []
        self.market_failures = {}
        rotation_index = self._next_rotation_index()
        for search in indeed_searches(count, rotation_index=rotation_index):
            if self.market_failures.get("__actor_limit__"):
                self.market_failures[search["market"].key] = self.market_failures["__actor_limit__"]
                continue
            try:
                run = self.client.actor(self.actor_id).call(
                    run_input={"max_results": search["max_results"], "keyword": search["keyword"], "country": search["market"].indeed_country, "remote_only": True}
                )
                # The actor can publish its plan-limit message while still
                # RUNNING. Inspect that signal before another market call.
                limit_message = _actor_limit_message(run, self.client)
                if limit_message:
                    self.market_failures = {"__source__": limit_message}
                    break
                status = _run_value(run, "status")
                if status not in {"SUCCEEDED", "SUCCESS"}:
                    raise ApifyRunFailedError(f"indeed Apify actor '{self.actor_id}' completed with status {status or 'missing'}.")
                dataset_id = _run_value(run, "default_dataset_id", "defaultDatasetId")
                if not dataset_id:
                    raise RuntimeError("indeed actor did not return a dataset ID.")
                items = list(self.client.dataset(dataset_id).iterate_items())
                limit_message = _actor_limit_message(run, self.client) if not items else ""
                if limit_message:
                    self.market_failures = {"__source__": limit_message}
                    break
                for item in items:
                    job = normalize_job_item(item, "Indeed")
                    job.market = search["market"].key
                    job.metadata["market"] = job.market
                    jobs.append(job)
            except Exception as exc:
                self.market_failures[search["market"].key] = str(exc)
        self._advance_rotation(rotation_index)
        return jobs


class SeekSourceAdapter(ApifyJobSourceAdapter):
    def __init__(self, client=None) -> None:
        super().__init__(
            "seek",
            SEEK_APIFY_ACTOR_ID,
            lambda count: {
                "urls": list(SEEK_SEARCH_URLS),
                "max_items_per_url": count,
                "ignore_url_failures": True,
            },
            client,
        )


class HaysSourceAdapter(ApifyJobSourceAdapter):
    def __init__(self, client=None) -> None:
        super().__init__(
            "hays",
            HAYS_APIFY_ACTOR_ID,
            lambda count: {"startUrls": list(HAYS_SEARCH_URLS), "maxItems": count},
            client,
        )


class RobertHalfSourceAdapter(ApifyJobSourceAdapter):
    """Structured Robert Half public-job extraction via a maintained actor."""

    def __init__(self, client=None) -> None:
        super().__init__(
            "robert_half",
            ROBERT_HALF_APIFY_ACTOR_ID,
            lambda count: {
                "urls": list(ROBERT_HALF_SEARCH_URLS),
                "max_items_per_url": count,
                "ignore_url_failures": True,
            },
            client,
        )


class TargetEmployerSourceAdapter(JobSourceAdapter):
    """Explicit-only bounded registry discovery; never enabled by default."""
    source_name = "target_employers"
    def __init__(self):
        self.options = {}
        self.market_failures = {}
        self.diagnostics = {}

    def configure(self, **options):
        self.options = options

    def discover(self, count: int) -> list[CareerOpportunity]:
        from app.services.target_employer_discovery import TargetEmployerDiscovery

        discovery = TargetEmployerDiscovery()
        jobs = discovery.discover(count, **self.options)
        # Report endpoint failures individually.  A broken employer must not
        # turn successful employer results into a failed source refresh.
        self.market_failures = dict(discovery.failures)
        self.diagnostics = dict(discovery.diagnostics)
        return jobs


def default_source_adapters() -> dict[str, JobSourceAdapter]:
    return {
        "linkedin": LinkedInSourceAdapter(),
        "indeed": IndeedSourceAdapter(),
        "seek": SeekSourceAdapter(),
        "hays": HaysSourceAdapter(),
        "robert_half": RobertHalfSourceAdapter(),
        "target_employers": TargetEmployerSourceAdapter(),
    }

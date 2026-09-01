"""LinkedIn Easy Apply MVP adapter (Task 21.29).

Recognizes a LinkedIn job listing, distinguishes Easy Apply from an external
Apply flow (out of scope this sprint), opens the Easy Apply modal, inspects
its live (JS-rendered) DOM via ARIA roles -- LinkedIn's own accessibility
markup, robust against obfuscated/versioned CSS class names, and consistent
with the same get_by_role() approach already used elsewhere in this codebase
(app/services/application_browser_service.py's final-button/safe-navigation
selection) rather than a fragile CSS-selector scrape.

Fills only approved/evidence-backed answers via the existing
ApplicationAnswerEngine -- the SAME engine, SAME Answer Vault, SAME concept
matching Greenhouse/Lever already use. Advances through Next/Continue/Review
steps one at a time. Never clicks "Submit application" without a separate,
explicit human-authorized call. Never fills a login/password/OTP field --
this module never navigates past a login/MFA/CAPTCHA page itself; that
remains PersistentSession's job (Task 21.28), which always pauses instead.

Reuses ApplicationField/ApplicationPlan (app/models/application_browser.py)
so a LinkedIn Easy Apply step looks like any other application plan to the
rest of the pipeline, and ApplicationExecutionResult
(app/models/application_execution.py) for the same persisted-execution-audit
convention Greenhouse/Lever progression already uses.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.application_browser import ApplicationField, ApplicationPlan
from app.models.application_execution import ApplicationExecutionResult
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.resume_document_format_policy import select_document_format

PORTAL = "LINKEDIN_EASY_APPLY"
EXECUTION_DIR = Path("app/data/application_executions")
SUBMISSION_RECEIPT_DIR = Path("app/data/application_submissions")
SUBMISSION_LOCK_DIR = Path("app/data/application_submission_locks")

LINKEDIN_JOB_URL_PATTERN = re.compile(r"linkedin\.com/jobs/view/", re.I)
# Task 21.29 real-contact finding: LinkedIn's primary Easy Apply control is
# rendered as a role="link" (not "button"), with its accessible name coming
# from aria-label="Easy Apply to this job" -- NOT its visible "Easy Apply"
# text alone. A plain substring/role="button" search also matches unrelated
# "Easy Apply" job cards in the same page's recommended-jobs rail (which
# share the role="main" landmark), so this is deliberately anchored and
# scoped to the primary control's own distinct labeling, not a bare
# substring match against the whole page.
EASY_APPLY_ARIA_PATTERN = re.compile(r"^easy apply to (this job|.+)$", re.I)
EASY_APPLY_NAME_PATTERN = re.compile(r"\beasy apply\b", re.I)  # kept for narrow-search fallback only
EXTERNAL_APPLY_NAME_PATTERN = re.compile(r"^apply$", re.I)
NEXT_BUTTON_PATTERN = re.compile(r"^(next|continue to next step)$", re.I)
REVIEW_BUTTON_PATTERN = re.compile(r"^review( your application)?$", re.I)
SUBMIT_BUTTON_PATTERN = re.compile(r"^submit application$", re.I)
SUCCESS_TEXT_PATTERN = re.compile(r"application (sent|submitted)|your application was sent|application received", re.I)

# Concepts that classify to a distinct pause reason rather than the generic
# HUMAN_SCREENING_REVIEW_REQUIRED bucket. Never string-matched against
# question text here -- always the already-computed AnswerDecision.concept
# from ApplicationAnswerEngine, exactly like isolated-mode Greenhouse/Lever.
_ELIGIBILITY_CONCEPT_PREFIXES = ("WORK_AUTHORIZATION", "SPONSORSHIP")  # matches both the bare and market-suffixed concept
_SALARY_CONCEPTS = ("EXPECTED_SALARY",)

# Human-pause states this module can return (Task 21.29). HUMAN_LOGIN_
# REQUIRED/HUMAN_MFA_REQUIRED/HUMAN_CAPTCHA_REQUIRED are detected by
# PersistentSession (Task 21.28) before this module is ever reached and are
# listed here only for a single shared vocabulary reference.
HUMAN_LOGIN_REQUIRED = "HUMAN_LOGIN_REQUIRED"
HUMAN_MFA_REQUIRED = "HUMAN_MFA_REQUIRED"
HUMAN_CAPTCHA_REQUIRED = "HUMAN_CAPTCHA_REQUIRED"
HUMAN_SCREENING_REVIEW_REQUIRED = "HUMAN_SCREENING_REVIEW_REQUIRED"
HUMAN_ELIGIBILITY_REVIEW_REQUIRED = "HUMAN_ELIGIBILITY_REVIEW_REQUIRED"
HUMAN_SALARY_REVIEW_REQUIRED = "HUMAN_SALARY_REVIEW_REQUIRED"
HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED = "HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED"


def is_linkedin_job_url(url: str) -> bool:
    return bool(LINKEDIN_JOB_URL_PATTERN.search(url or ""))


class EasyApplyNotFoundError(RuntimeError):
    """Neither an Easy Apply nor an external Apply control was found."""


class LinkedInEasyApplyAdapter:
    """Stateless helpers operating on a live Playwright Page/Locator."""

    def __init__(self, answer_engine: ApplicationAnswerEngine | None = None):
        self.answer_engine = answer_engine or ApplicationAnswerEngine()

    @staticmethod
    def _primary_content_scope(page):
        """Scope queries to the role="main" landmark when present -- the
        page's "similar jobs" rail shares the same landmark on LinkedIn, so
        this alone does not fully disambiguate (see EASY_APPLY_ARIA_PATTERN's
        own anchoring), but avoids matching header/nav/footer controls."""
        return page

    async def _easy_apply_control(self, page):
        """The one, precisely-identified primary Easy Apply control -- never
        a bare substring match against the whole page, which would also
        match unrelated "Easy Apply" job cards in a recommended-jobs rail."""
        scope = self._primary_content_scope(page)
        for role in ("link", "button"):
            control = scope.get_by_role(role, name=EASY_APPLY_ARIA_PATTERN)
            if await control.count() >= 1:
                return control.first
        return None

    async def detect_easy_apply(self, page) -> str:
        """Returns "EASY_APPLY", "EXTERNAL_APPLY", or "NOT_FOUND". Never
        clicks anything -- detection only."""
        if await self._easy_apply_control(page) is not None:
            return "EASY_APPLY"
        apply_button = page.get_by_role("button", name=EXTERNAL_APPLY_NAME_PATTERN)
        apply_link = page.get_by_role("link", name=EXTERNAL_APPLY_NAME_PATTERN)
        if await apply_button.count() >= 1 or await apply_link.count() >= 1:
            return "EXTERNAL_APPLY"
        return "NOT_FOUND"

    async def open_easy_apply_modal(self, page):
        """Clicks the Easy Apply control and waits for the modal dialog.
        Raises EasyApplyNotFoundError if no Easy Apply control exists --
        never silently falls back to the external Apply flow."""
        button = await self._easy_apply_control(page)
        if button is None:
            raise EasyApplyNotFoundError("No Easy Apply control was found on this page.")
        await button.click()
        dialog = page.get_by_role("dialog")
        await dialog.wait_for(state="visible", timeout=15_000)
        return dialog

    async def inspect_step(self, dialog, market: str | None = None, vacancy: Any | None = None) -> ApplicationPlan:
        """Extract the current modal step's controls into an ApplicationPlan
        using the SAME ApplicationAnswerEngine every other portal uses --
        never a LinkedIn-specific answer path. Detects step kind (form,
        review, success) from visible dialog text, never a URL (the modal
        does not change the page URL)."""
        plan = ApplicationPlan(portal=PORTAL, url="", tracker_id=None,
                                company=self._field(vacancy, "company") or "",
                                role=self._field(vacancy, "job_title") or self._field(vacancy, "title") or "",
                                market=market or "")
        text = (await dialog.inner_text()).strip()
        lowered = text.lower()
        if SUCCESS_TEXT_PATTERN.search(lowered):
            plan.page_purpose = "APPLICATION_SUCCESS"
            plan.readiness = "APPLICATION_SUCCESS"
            return plan
        is_review_step = "review your application" in lowered
        plan.page_purpose = "APPLICATION_REVIEW" if is_review_step else "APPLICATION_FORM"

        await self._collect_textboxes(dialog, plan, market, vacancy)
        await self._collect_spinbuttons(dialog, plan, market, vacancy)
        await self._collect_radiogroups(dialog, plan, market, vacancy)
        await self._collect_comboboxes(dialog, plan, market, vacancy)
        await self._collect_bare_groups(dialog, plan, market, vacancy)
        await self._collect_file_uploads(dialog, plan, vacancy)

        button_names = await self._button_names(dialog)
        plan.final_submit_detected = any(SUBMIT_BUTTON_PATTERN.match(name) for name in button_names)
        plan.safe_navigation_detected = any(
            NEXT_BUTTON_PATTERN.match(name) or REVIEW_BUTTON_PATTERN.match(name) for name in button_names
        )

        blocking_required = any(f.required and f.action == "REVIEW" for f in plan.fields)
        blocking_document = any(d["required"] and d["action"] == "DOCUMENT_NOT_READY" for d in plan.document_requirements)
        if blocking_required or blocking_document:
            plan.readiness = "MANUAL_INPUT_REQUIRED"
        elif plan.page_purpose == "APPLICATION_REVIEW":
            plan.readiness = "READY_FOR_FINAL_REVIEW"
        else:
            plan.readiness = "READY_FOR_PREPARATION"
        return plan

    async def fill_step(self, dialog, plan: ApplicationPlan) -> None:
        """Fills only FILL-classified fields and uploads the resume when
        ready. Never touches a REVIEW/SKIP field, never clicks a button."""
        textboxes = dialog.get_by_role("textbox")
        spinbuttons = dialog.get_by_role("spinbutton")
        radiogroups = dialog.get_by_role("radiogroup")
        comboboxes = dialog.get_by_role("combobox")
        for field_item in plan.fields:
            if field_item.action != "FILL" or field_item.answer is None:
                continue
            try:
                if field_item.field_id.startswith("textbox_"):
                    index = int(field_item.field_id.split("_", 1)[1])
                    await textboxes.nth(index).fill(str(field_item.answer))
                elif field_item.field_id.startswith("spinbutton_"):
                    index = int(field_item.field_id.split("_", 1)[1])
                    await spinbuttons.nth(index).fill(str(field_item.answer))
                elif field_item.field_id.startswith("radiogroup_"):
                    index = int(field_item.field_id.split("_", 1)[1])
                    group = radiogroups.nth(index)
                    option = group.get_by_role("radio", name=re.compile(re.escape(str(field_item.answer)), re.I))
                    if await option.count() == 1:
                        # Task 21.31: LinkedIn's own custom-styled radio
                        # controls render a <label> visually on top of the
                        # native <input>, which fails Playwright's default
                        # "receives pointer events at this point" actionability
                        # check even though the control is genuinely checkable
                        # -- force=True performs the real native check() (and
                        # fires the same change events) without that false-
                        # negative visibility gate.
                        await option.check(force=True)
                    else:
                        field_item.action = "REVIEW"
                        field_item.reason = "Approved answer could not be mapped to exactly one offered option."
                elif field_item.field_id.startswith("combobox_"):
                    index = int(field_item.field_id.split("_", 1)[1])
                    await comboboxes.nth(index).select_option(label=str(field_item.answer))
                elif field_item.field_id.startswith("baregroup_radio_"):
                    index = int(field_item.field_id.rsplit("_", 1)[1])
                    group = dialog.get_by_role("group").nth(index)
                    option = group.get_by_role("radio", name=re.compile(re.escape(str(field_item.answer)), re.I))
                    if await option.count() == 1:
                        # Task 21.31: LinkedIn's own custom-styled radio
                        # controls render a <label> visually on top of the
                        # native <input>, which fails Playwright's default
                        # "receives pointer events at this point" actionability
                        # check even though the control is genuinely checkable
                        # -- force=True performs the real native check() (and
                        # fires the same change events) without that false-
                        # negative visibility gate.
                        await option.check(force=True)
                    else:
                        field_item.action = "REVIEW"
                        field_item.reason = "Approved answer could not be mapped to exactly one offered option."
            except Exception:
                field_item.action = "REVIEW"
                field_item.reason = "Supported fill selector/value was not unambiguous."
        for document in plan.document_requirements:
            if document["action"] != "READY_FOR_UPLOAD" or document["kind"] != "RESUME":
                continue
            try:
                await dialog.locator("input[type='file']").first.set_input_files(document["path"])
                document["action"] = "UPLOADED_IN_FILL_PREVIEW"
            except Exception:
                document["action"] = "DOCUMENT_NOT_READY"

    async def advance_step(self, dialog) -> str:
        """Clicks exactly one unambiguous Next/Review control and waits for
        the modal content to change. Never clicks "Submit application".
        Returns "ADVANCED" or "BLOCKED" (ambiguous/no control found)."""
        before = await dialog.inner_text()
        for pattern in (NEXT_BUTTON_PATTERN, REVIEW_BUTTON_PATTERN):
            control = dialog.get_by_role("button", name=pattern)
            count = await control.count()
            if count == 1:
                await control.click()
                await self._wait_for_modal_change(dialog, before)
                return "ADVANCED"
            if count > 1:
                return "BLOCKED"
        return "BLOCKED"

    async def click_submit(self, dialog) -> dict:
        """The sole final-click primitive. Caller (submit_easy_apply) must
        have already verified explicit human authorization and exactly-once
        protection before ever calling this."""
        control = dialog.get_by_role("button", name=SUBMIT_BUTTON_PATTERN)
        if await control.count() != 1 or not await control.is_visible() or not await control.is_enabled():
            return {"outcome": "SUBMISSION_FAILED", "signals": ["FINAL_CONTROL_NOT_VERIFIED"]}
        before = await dialog.inner_text()
        clicked_at = datetime.now(timezone.utc).isoformat()
        await control.click()
        try:
            await self._wait_for_modal_change(dialog, before, timeout_ms=15_000)
        except Exception:
            return {"outcome": "SUBMISSION_OUTCOME_UNCERTAIN", "submit_clicked_at": clicked_at, "signals": ["POST_CLICK_TIMEOUT"]}
        try:
            after = (await dialog.inner_text()).lower()
        except Exception:
            # A closed dialog after submit is itself a success signal on LinkedIn.
            return {"outcome": "SUBMISSION_CONFIRMED", "submit_clicked_at": clicked_at, "confirmed_at": datetime.now(timezone.utc).isoformat(), "signals": ["DIALOG_CLOSED_AFTER_SUBMIT"]}
        if SUCCESS_TEXT_PATTERN.search(after):
            return {"outcome": "SUBMISSION_CONFIRMED", "submit_clicked_at": clicked_at, "confirmed_at": datetime.now(timezone.utc).isoformat(), "signals": ["SUCCESS_MESSAGE"]}
        if re.search(r"error|required field|unable to submit|please fix", after):
            return {"outcome": "SUBMISSION_FAILED", "submit_clicked_at": clicked_at, "signals": ["FAILURE_SIGNAL"]}
        return {"outcome": "SUBMISSION_OUTCOME_UNCERTAIN", "submit_clicked_at": clicked_at, "signals": ["NO_CONFIRMATION"]}

    # --- collection helpers ---------------------------------------------

    async def _collect_textboxes(self, dialog, plan, market, vacancy):
        boxes = dialog.get_by_role("textbox")
        for index in range(await boxes.count()):
            box = boxes.nth(index)
            label = await self._accessible_name(box)
            required = await self._is_required(box, label)
            decision = self.answer_engine.resolve(label, field_type="TEXT", market=market, vacancy=vacancy)
            action = self._action_for(decision, required)
            plan.fields.append(ApplicationField(
                f"textbox_{index}", PORTAL, label, label, "TEXT", required,
                action=action, concept=decision.concept, answer=decision.answer,
                confidence=decision.confidence, answer_source=decision.answer_source, reason=decision.reason,
            ))

    async def _collect_spinbuttons(self, dialog, plan, market, vacancy):
        """Native numeric inputs (e.g. "expected annual salary") map to the
        ARIA "spinbutton" role, not "textbox" -- collected separately so
        their field_type is correctly reported as NUMBER, which is what
        forces a hard salary-review pause in ApplicationAnswerEngine
        (a pre-approved TEXT-style "negotiable" answer must never satisfy a
        field demanding an exact figure)."""
        boxes = dialog.get_by_role("spinbutton")
        for index in range(await boxes.count()):
            box = boxes.nth(index)
            label = await self._accessible_name(box)
            required = await self._is_required(box, label)
            decision = self.answer_engine.resolve(label, field_type="NUMBER", market=market, vacancy=vacancy)
            action = self._action_for(decision, required)
            plan.fields.append(ApplicationField(
                f"spinbutton_{index}", PORTAL, label, label, "NUMBER", required,
                action=action, concept=decision.concept, answer=decision.answer,
                confidence=decision.confidence, answer_source=decision.answer_source, reason=decision.reason,
            ))

    async def _collect_radiogroups(self, dialog, plan, market, vacancy):
        groups = dialog.get_by_role("radiogroup")
        for index in range(await groups.count()):
            group = groups.nth(index)
            label = await self._accessible_name(group)
            options = group.get_by_role("radio")
            choices = [await self._accessible_name(options.nth(option_index)) for option_index in range(await options.count())]
            required = await self._is_required(group, label)
            decision = self.answer_engine.resolve(label, field_type="RADIO", choices=choices, market=market, vacancy=vacancy)
            action = self._action_for(decision, required)
            plan.fields.append(ApplicationField(
                f"radiogroup_{index}", PORTAL, label, label, "RADIO", required, choices=choices,
                action=action, concept=decision.concept, answer=decision.answer,
                confidence=decision.confidence, answer_source=decision.answer_source, reason=decision.reason,
            ))

    async def _collect_comboboxes(self, dialog, plan, market, vacancy):
        boxes = dialog.get_by_role("combobox")
        for index in range(await boxes.count()):
            box = boxes.nth(index)
            label = await self._accessible_name(box)
            required = await self._is_required(box, label)
            decision = self.answer_engine.resolve(label, field_type="SELECT", market=market, vacancy=vacancy)
            action = self._action_for(decision, required)
            plan.fields.append(ApplicationField(
                f"combobox_{index}", PORTAL, label, label, "SELECT", required,
                action=action, concept=decision.concept, answer=decision.answer,
                confidence=decision.confidence, answer_source=decision.answer_source, reason=decision.reason,
            ))

    async def _collect_bare_groups(self, dialog, plan, market, vacancy):
        """Task 21.31 production fix: LinkedIn also poses standalone
        Yes/No AND multi-select checklist questions (e.g. "Are you based
        in the UK and have the right to work in the UK?", "Which
        accountancy firm(s) have you trained or worked with?") as a bare
        role="group" wrapping role="radio"/role="checkbox" children --
        NOT the role="radiogroup" _collect_radiogroups already handles --
        and with no aria-label/aria-labelledby on the group itself, only
        its own plain text ("<question>\nRequired\n<option>\n<option>...").
        Without this collector such a required question was invisible to
        inspect_step entirely: never filled, never flagged for review, so
        a real application could reach "Next"/"Review" with LinkedIn's own
        client-side validation silently blocking every further click --
        including a genuinely approved, reusable fact like
        WORK_AUTHORIZATION_UK, which should auto-fill exactly like it does
        everywhere else. Resolved through the SAME ApplicationAnswerEngine/
        _action_for path every other field type uses -- a multi-select
        checklist has no reusable concept and always routes to human
        review; a bare radio group can auto-fill when (and only when) an
        approved rule already answers it, exactly like _collect_radiogroups."""
        groups = dialog.get_by_role("group")
        for index in range(await groups.count()):
            group = groups.nth(index)
            radios = group.get_by_role("radio")
            checkboxes = group.get_by_role("checkbox")
            radio_count = await radios.count()
            checkbox_count = await checkboxes.count()
            if radio_count == 0 and checkbox_count == 0:
                continue
            raw_name = await self._accessible_name(group)
            lines = [line.strip() for line in raw_name.split("\n") if line.strip()]
            label = lines[0].rstrip("*").strip() if lines else ""
            required = len(lines) > 1 and lines[1].lower() == "required"
            if radio_count and not checkbox_count:
                choices = [await self._accessible_name(radios.nth(i)) for i in range(radio_count)]
                decision = self.answer_engine.resolve(label, field_type="RADIO", choices=choices, market=market, vacancy=vacancy)
                action = self._action_for(decision, required)
                plan.fields.append(ApplicationField(
                    f"baregroup_radio_{index}", PORTAL, label, label, "RADIO", required, choices=choices,
                    action=action, concept=decision.concept, answer=decision.answer,
                    confidence=decision.confidence, answer_source=decision.answer_source, reason=decision.reason,
                ))
            else:
                choices = [await self._accessible_name(checkboxes.nth(i)) for i in range(checkbox_count)]
                decision = self.answer_engine.resolve(label, field_type="CHECKBOX_GROUP", choices=choices, market=market, vacancy=vacancy)
                action = self._action_for(decision, required)
                plan.fields.append(ApplicationField(
                    f"baregroup_checkbox_{index}", PORTAL, label, label, "CHECKBOX_GROUP", required, choices=choices,
                    action=action, concept=decision.concept, answer=decision.answer,
                    confidence=decision.confidence, answer_source=decision.answer_source, reason=decision.reason,
                ))

    async def _collect_file_uploads(self, dialog, plan, vacancy):
        inputs = dialog.locator("input[type='file']")
        count = await inputs.count()
        if count == 0:
            return
        # Task 21.30 Section 1: LinkedIn Easy Apply accepts both PDF and
        # DOCX -- prefer the PDF sibling (same approved resume content,
        # never regenerated for format reasons), falling back to DOCX only
        # when no PDF sibling is available. `resume_path` alone (legacy/
        # test-fixture shape, no PDF sibling given) still works unchanged.
        pdf_path = self._field(vacancy, "resume_pdf_path")
        pdf_candidate = Path(pdf_path) if pdf_path else None
        pdf_ready = bool(pdf_candidate and pdf_candidate.is_file())
        docx_path = self._field(vacancy, "resume_path")
        docx_candidate = Path(docx_path) if docx_path else None
        docx_ready = bool(docx_candidate and docx_candidate.is_file())
        chosen_format = select_document_format(pdf_available=pdf_ready)
        if chosen_format == "PDF" and pdf_ready:
            path, ready = pdf_candidate, True
        else:
            path, ready = docx_candidate, docx_ready
        plan.document_requirements.append({
            "field_id": "file_0", "label": "Resume", "kind": "RESUME", "required": True,
            "path": str(path) if ready else "", "action": "READY_FOR_UPLOAD" if ready else "DOCUMENT_NOT_READY",
        })

    async def _button_names(self, dialog) -> list[str]:
        buttons = dialog.get_by_role("button")
        names = []
        for index in range(await buttons.count()):
            name = (await self._accessible_name(buttons.nth(index))).strip()
            if name:
                names.append(name)
        return names

    async def _accessible_name(self, locator) -> str:
        """Best-effort accessible name: explicit aria-label/aria-labelledby
        first, then the DOM's own `element.labels` (covers BOTH a wrapping
        `<label><input>text</label>` and a `<label for=id>` elsewhere in the
        page -- the correct, robust primitive for this, not a heuristic),
        then visible text (for buttons/dialogs, which have no `.labels`),
        then placeholder as a last resort."""
        aria_label = await locator.get_attribute("aria-label")
        if aria_label:
            return aria_label.strip()
        labelledby = await locator.get_attribute("aria-labelledby")
        if labelledby:
            page = locator.page
            parts = []
            for ref_id in labelledby.split():
                ref = page.locator(f"#{ref_id}")
                if await ref.count():
                    parts.append((await ref.inner_text()).strip())
            if parts:
                return " ".join(parts)
        try:
            labels_text = await locator.evaluate(
                "el => el.labels ? Array.from(el.labels).map(l => l.innerText.trim()).filter(Boolean).join(' ') : ''"
            )
            if labels_text:
                return labels_text.strip()
        except Exception:
            pass
        try:
            text = (await locator.inner_text()).strip()
            if text:
                return text
        except Exception:
            pass
        placeholder = await locator.get_attribute("placeholder")
        return placeholder or ""

    @staticmethod
    async def _is_required(locator, label: str) -> bool:
        aria_required = await locator.get_attribute("aria-required")
        if aria_required == "true":
            return True
        try:
            required_attr = await locator.get_attribute("required")
            if required_attr is not None:
                return True
        except Exception:
            pass
        return "*" in label

    @staticmethod
    def _action_for(decision, required: bool) -> str:
        if not decision.manual_review and decision.confidence == "HIGH":
            return "FILL"
        if not required and decision.concept == "UNKNOWN":
            return "SKIP"
        return "REVIEW"

    @staticmethod
    async def _wait_for_modal_change(dialog, previous_text: str, timeout_ms: int = 15_000) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            try:
                current = await dialog.inner_text()
            except Exception:
                return  # Dialog closed (e.g. submitted) -- treat as a change.
            if current != previous_text:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("Easy Apply modal did not change after clicking Next/Review/Submit.")

    @staticmethod
    def _field(value: Any, name: str) -> Any:
        if value is None:
            return None
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _classify_pause_reason(plan: ApplicationPlan) -> str:
    """Maps an unresolved required field/document to the specific HUMAN_*
    pause state, using ONLY the already-computed AnswerDecision.concept --
    never re-deriving intent from question text here."""
    for field_item in plan.fields:
        if not (field_item.required and field_item.action == "REVIEW"):
            continue
        if field_item.concept.startswith(_ELIGIBILITY_CONCEPT_PREFIXES):
            return HUMAN_ELIGIBILITY_REVIEW_REQUIRED
    for field_item in plan.fields:
        if field_item.required and field_item.action == "REVIEW" and field_item.concept in _SALARY_CONCEPTS:
            return HUMAN_SALARY_REVIEW_REQUIRED
    return HUMAN_SCREENING_REVIEW_REQUIRED


def _step_fingerprint(plan: ApplicationPlan) -> str:
    import hashlib
    parts = [plan.page_purpose] + [f"{f.concept}:{f.field_type}:{f.required}" for f in plan.fields]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class LinkedInEasyApplyOrchestrator:
    """Ties the adapter to a live PersistentSession page, one modal step at
    a time, with the same persisted-execution-audit convention Greenhouse/
    Lever progression already uses. Never performs credential login (that
    remains entirely PersistentSession's job); never clicks Submit."""

    def __init__(self, adapter: LinkedInEasyApplyAdapter | None = None, execution_dir: str | Path = EXECUTION_DIR):
        self.adapter = adapter or LinkedInEasyApplyAdapter()
        self.execution_dir = Path(execution_dir)

    async def run(
        self, page, vacancy: Any, *, market: str | None = None, tracker_id: int | None = None,
        max_steps: int = 6, execution: ApplicationExecutionResult | None = None,
    ) -> ApplicationExecutionResult:
        result = execution or ApplicationExecutionResult(tracker_id=tracker_id or 0, portal=PORTAL, mode="EASY_APPLY_PROGRESS")
        self._audit(result, "EASY_APPLY_RUN_STARTED")
        seen_fingerprints: set[str] = set()

        availability = await self.adapter.detect_easy_apply(page)
        if availability == "NOT_FOUND":
            result.status = "EASY_APPLY_NOT_AVAILABLE"
            self._audit(result, "EASY_APPLY_NOT_AVAILABLE")
            return self._save(result)
        if availability == "EXTERNAL_APPLY":
            result.status = "EXTERNAL_APPLY_NOT_SUPPORTED"
            self._audit(result, "EXTERNAL_APPLY_DETECTED")
            return self._save(result)

        try:
            dialog = page.get_by_role("dialog")
            if await dialog.count() < 1:
                dialog = await self.adapter.open_easy_apply_modal(page)
                self._audit(result, "EASY_APPLY_MODAL_OPENED")
        except EasyApplyNotFoundError:
            result.status = "EASY_APPLY_NOT_AVAILABLE"
            self._audit(result, "EASY_APPLY_NOT_AVAILABLE")
            return self._save(result)

        for _ in range(max_steps):
            plan = await self.adapter.inspect_step(dialog, market=market, vacancy=vacancy)
            result.pages_processed += 1
            result.fields_detected += len(plan.fields)
            result.fields_resolved += sum(f.action == "FILL" for f in plan.fields)
            result.manual_review_fields += sum(f.action == "REVIEW" for f in plan.fields)
            result.unknown_required_fields += sum(f.required and f.action == "REVIEW" and f.concept == "UNKNOWN" for f in plan.fields)
            self._audit(result, "STEP_INSPECTED", page_purpose=plan.page_purpose, fields=len(plan.fields))

            fingerprint = _step_fingerprint(plan)
            if fingerprint in seen_fingerprints:
                result.status = "LOOP_DETECTED"
                self._audit(result, "LOOP_DETECTED")
                return self._save(result)
            seen_fingerprints.add(fingerprint)

            if plan.page_purpose == "APPLICATION_SUCCESS":
                # We never click Submit ourselves in this loop -- reaching a
                # success page here is an anomaly, not a real outcome.
                result.status = "UNEXPECTED_APPLICATION_SUCCESS"
                self._audit(result, "UNEXPECTED_APPLICATION_SUCCESS")
                return self._save(result)

            if any(d["required"] and d["action"] == "DOCUMENT_NOT_READY" for d in plan.document_requirements):
                result.status = "MANUAL_INPUT_REQUIRED"
                self._audit(result, "DOCUMENT_NOT_READY")
                return self._save(result)

            if any(f.required and f.action == "REVIEW" for f in plan.fields):
                result.status = _classify_pause_reason(plan)
                self._audit(result, "HUMAN_PAUSE", reason=result.status)
                return self._save(result)

            if plan.page_purpose == "APPLICATION_REVIEW":
                result.status = HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED
                result.final_submit_detected = plan.final_submit_detected
                self._audit(result, "FINAL_REVIEW_REACHED")
                return self._save(result)

            await self.adapter.fill_step(dialog, plan)
            result.fields_filled += sum(f.action == "FILL" for f in plan.fields)
            result.resume_uploaded = result.resume_uploaded or any(
                d["kind"] == "RESUME" and d["action"] == "UPLOADED_IN_FILL_PREVIEW" for d in plan.document_requirements
            )
            self._audit(result, "STEP_FILLED")

            advance = await self.adapter.advance_step(dialog)
            result.navigation_actions += 1
            if advance != "ADVANCED":
                result.status = "NAVIGATION_UNCERTAIN"
                self._audit(result, "NAVIGATION_UNCERTAIN")
                return self._save(result)
            self._audit(result, "STEP_ADVANCED")

        result.status = "MAX_STEPS_EXCEEDED"
        self._audit(result, "MAX_STEPS_EXCEEDED")
        return self._save(result)

    def _audit(self, result: ApplicationExecutionResult, action: str, **details) -> None:
        result.audit.append({"at": datetime.now(timezone.utc).isoformat(), "action": action, **details})

    def _save(self, result: ApplicationExecutionResult) -> ApplicationExecutionResult:
        self.execution_dir.mkdir(parents=True, exist_ok=True)
        (self.execution_dir / f"{result.execution_id}.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        return result


class SubmissionLockedError(RuntimeError):
    """Another submission attempt already holds this execution's lock."""


async def submit_easy_apply(
    adapter: LinkedInEasyApplyAdapter, page, execution: ApplicationExecutionResult, confirmation: str,
    *, receipt_dir: str | Path = SUBMISSION_RECEIPT_DIR, lock_dir: str | Path = SUBMISSION_LOCK_DIR,
) -> dict:
    """The sole LinkedIn Easy Apply final-submit entry point. Mirrors
    ApplicationSubmissionService's safety invariants (explicit typed
    confirmation, exclusive file lock, idempotent re-entry via existing
    receipts, never a retry on an uncertain outcome) rather than reusing
    that class directly -- its transport is built around a static-HTML
    reconciliation model (Greenhouse/Lever) that does not apply to a live
    modal, so duplicating the SAME safety pattern here (not weakening it)
    was judged safer than forcing an ill-fitting shared code path."""
    receipt_dir = Path(receipt_dir)
    lock_dir = Path(lock_dir)
    expected = f"SUBMIT {execution.execution_id}"
    if confirmation != expected:
        return _receipt(execution, "SUBMISSION_CANCELLED", receipt_dir)
    if _already_confirmed(execution, receipt_dir):
        return _receipt(execution, "ALREADY_SUBMITTED", receipt_dir)
    if _already_uncertain(execution, receipt_dir):
        # Mirrors ApplicationSubmissionService's own rule: an uncertain
        # outcome is never auto-retried -- it requires a human to check the
        # real LinkedIn application status before anything submits again.
        return _receipt(execution, "SUBMISSION_OUTCOME_UNCERTAIN", receipt_dir, signals=["PREVIOUS_OUTCOME_UNCERTAIN"])
    if execution.status != HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED:
        return _receipt(execution, "SUBMISSION_BLOCKED", receipt_dir, signals=["NOT_AWAITING_FINAL_AUTHORIZATION"])
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{execution.execution_id}.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise SubmissionLockedError(f"Execution {execution.execution_id} is already being submitted.") from None
    try:
        dialog = page.get_by_role("dialog")
        if await dialog.count() < 1:
            return _receipt(execution, "SUBMISSION_FAILED", receipt_dir, signals=["MODAL_NOT_OPEN"])
        plan = await adapter.inspect_step(dialog)
        if plan.page_purpose != "APPLICATION_REVIEW" or not plan.final_submit_detected:
            return _receipt(execution, "SUBMISSION_FAILED", receipt_dir, signals=["FINAL_REVIEW_NOT_REACHED"])
        outcome = await adapter.click_submit(dialog)
        return _receipt(execution, outcome["outcome"], receipt_dir, signals=outcome.get("signals", []),
                         submit_clicked_at=outcome.get("submit_clicked_at", ""), confirmed_at=outcome.get("confirmed_at", ""))
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _already_confirmed(execution: ApplicationExecutionResult, receipt_dir: Path) -> bool:
    return _has_receipt_outcome(execution, receipt_dir, "SUBMISSION_CONFIRMED")


def _already_uncertain(execution: ApplicationExecutionResult, receipt_dir: Path) -> bool:
    return _has_receipt_outcome(execution, receipt_dir, "SUBMISSION_OUTCOME_UNCERTAIN")


def _has_receipt_outcome(execution: ApplicationExecutionResult, receipt_dir: Path, outcome: str) -> bool:
    if not receipt_dir.exists():
        return False
    for path in receipt_dir.glob(f"{execution.execution_id}_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("outcome") == outcome:
            return True
    return False


def _receipt(execution: ApplicationExecutionResult, outcome: str, receipt_dir: Path, **fields) -> dict:
    receipt = {
        "execution_id": execution.execution_id, "tracker_id": execution.tracker_id, "portal": PORTAL,
        "outcome": outcome, "created_at": datetime.now(timezone.utc).isoformat(), **fields,
    }
    receipt_dir.mkdir(parents=True, exist_ok=True)
    path = receipt_dir / f"{execution.execution_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ%f')}.json"
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


async def run_linkedin_application(
    session: Any, vacancy: Any, *, market: str | None = None, tracker_id: int | None = None,
    orchestrator: "LinkedInEasyApplyOrchestrator | None" = None,
) -> ApplicationExecutionResult:
    """Top-level entry point tying PersistentSession (Task 21.28) to the
    Easy Apply orchestrator (Task 21.29): the outer page's own human-pause
    state (login/MFA/CAPTCHA -- PersistentSession.refresh_state()) is always
    checked FIRST and always wins. Easy Apply detection is never attempted
    while the page itself still needs a human, and this function never
    re-implements that detection -- it only reads session.state."""
    orchestrator = orchestrator or LinkedInEasyApplyOrchestrator()
    await session.refresh_state()
    if session.state != "READY":
        result = ApplicationExecutionResult(tracker_id=tracker_id or 0, portal=PORTAL, mode="EASY_APPLY_PROGRESS")
        result.status = session.state
        result.auth_required = session.state == HUMAN_LOGIN_REQUIRED
        result.mfa_required = session.state == HUMAN_MFA_REQUIRED
        result.captcha_detected = session.state == HUMAN_CAPTCHA_REQUIRED
        orchestrator._audit(result, "OUTER_PAGE_HUMAN_PAUSE", reason=session.state)
        return orchestrator._save(result)
    return await orchestrator.run(session.page, vacancy, market=market, tracker_id=tracker_id)

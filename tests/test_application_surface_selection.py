"""Real-Chromium coverage for trusted Greenhouse iframe discovery (Task 21.8C.2).

Deterministic, localhost-only. No employer site is accessed and no application
is ever filled/submitted here -- these tests prove only that the correct
Page/Frame surface is selected (or that selection fails closed).
"""
import asyncio

from app.services.application_browser_service import ApplicationBrowserService
from helpers.local_ats_server import LocalATS, LocalGreenhouseIframeWrapper
from helpers.synthetic_answer_engine import SyntheticAnswerEngine


def _select(tmp_path, url, expected_portal="GREENHOUSE"):
    async def run():
        from playwright.async_api import async_playwright
        browser_service = ApplicationBrowserService(preview_folder=tmp_path, answer_engine=SyntheticAnswerEngine())
        async with async_playwright() as api:
            chromium = await api.chromium.launch(headless=True); context = await chromium.new_context(); page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded")
                selection = await browser_service.select_application_surface(page, expected_portal)
                surface = selection["surface"]
                surface_is_page = surface is page
                surface_html = await surface.content() if surface is not None else None
                return selection["status"], surface_is_page, surface_html, page.url
            finally:
                await context.close(); await chromium.close()
    return asyncio.run(run())


def test_a_direct_greenhouse_page_is_selected_without_iframe_search(tmp_path):
    ats = LocalATS(); url = ats.start()
    try:
        status, surface_is_page, html, _ = _select(tmp_path, url)
        assert status == "DIRECT_PAGE" and surface_is_page
        assert "First name" in html
    finally:
        ats.close()


def test_b_trusted_greenhouse_iframe_is_selected(tmp_path):
    wrapper = LocalGreenhouseIframeWrapper("single"); url = wrapper.start()
    try:
        status, surface_is_page, html, final_page_url = _select(tmp_path, url)
        assert status == "TRUSTED_IFRAME" and not surface_is_page
        assert "First name" in html
        assert final_page_url == url
    finally:
        wrapper.close()


def test_c_unrelated_iframe_is_ignored_in_favor_of_trusted_greenhouse_iframe(tmp_path):
    wrapper = LocalGreenhouseIframeWrapper("with_unrelated"); url = wrapper.start()
    try:
        status, surface_is_page, html, _ = _select(tmp_path, url)
        assert status == "TRUSTED_IFRAME" and not surface_is_page
        assert "First name" in html
    finally:
        wrapper.close()


def test_d_missing_application_surface_fails_closed(tmp_path):
    wrapper = LocalGreenhouseIframeWrapper("none"); url = wrapper.start()
    try:
        status, _, surface_html, _ = _select(tmp_path, url)
        assert status == "APPLICATION_SURFACE_NOT_FOUND" and surface_html is None
    finally:
        wrapper.close()


def test_e_ambiguous_trusted_iframes_fail_closed_without_selection(tmp_path):
    wrapper = LocalGreenhouseIframeWrapper("ambiguous"); url = wrapper.start()
    try:
        status, _, surface_html, _ = _select(tmp_path, url)
        assert status == "APPLICATION_SURFACE_AMBIGUOUS" and surface_html is None
    finally:
        wrapper.close()


def test_f_captcha_iframe_is_never_selected_as_application_surface(tmp_path):
    wrapper = LocalGreenhouseIframeWrapper("with_captcha"); url = wrapper.start()
    try:
        status, surface_is_page, html, _ = _select(tmp_path, url)
        assert status == "TRUSTED_IFRAME" and not surface_is_page
        assert "CAPTCHA" not in html and "First name" in html
    finally:
        wrapper.close()


def test_g_stable_route_is_not_replaced_by_the_selected_iframe(tmp_path):
    wrapper = LocalGreenhouseIframeWrapper("single"); url = wrapper.start()
    stable_route = {"application_url": url}
    try:
        status, surface_is_page, _, final_page_url = _select(tmp_path, url)
        assert status == "TRUSTED_IFRAME" and not surface_is_page
        # The top-level page never navigated away from the stable route, and
        # nothing here mutated it -- the frame is an in-memory surface only.
        assert final_page_url == url == stable_route["application_url"]
    finally:
        wrapper.close()

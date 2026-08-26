"""Real-Chromium smoke coverage for the shared Page/Frame surface primitives."""
import asyncio

from app.services.application_browser_service import ApplicationBrowserService
from helpers.local_ats_server import LocalFrameSurface
from helpers.synthetic_answer_engine import SyntheticAnswerEngine


def _pdf(path):
    path.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    return path


def _run_surface_smoke(tmp_path, use_frame):
    async def run():
        from playwright.async_api import async_playwright
        fixture=LocalFrameSurface(); outer_url=fixture.start()
        resume=_pdf(tmp_path / "Synthetic_Frame_Resume.pdf")
        browser_service=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine())
        try:
            async with async_playwright() as api:
                chromium=await api.chromium.launch(headless=True); context=await chromium.new_context(); page=await context.new_page()
                try:
                    await page.goto(outer_url if use_frame else outer_url.replace("/outer", "/inner?stage=1"), wait_until="domcontentloaded")
                    surface=next(frame for frame in page.frames if frame != page.main_frame) if use_frame else page
                    html=await browser_service._surface_content(surface)
                    plan=browser_service.preview_html(html, browser_service._surface_url(surface), {"market":"UK", "resume_path":str(resume)}, persist=False)
                    await browser_service._fill_supported(surface, plan)
                    assert await surface.locator("#first").input_value() == "Test"
                    assert await surface.locator("#auth").input_value() == "Yes"
                    assert await surface.locator("#cv").evaluate("node => node.files[0].name") == resume.name
                    before_url=browser_service._surface_url(surface); before_html=html
                    assert await browser_service._click_safe_navigation(surface, html) == "Continue"
                    await browser_service._wait_for_meaningful_transition(surface, before_url, browser_service._page_fingerprint(before_html))
                    assert "stage=2" in browser_service._surface_url(surface)
                    assert "Frame stage 2" in await browser_service._surface_content(surface)
                finally:
                    await context.close(); await chromium.close()
        finally:
            fixture.close()
    asyncio.run(run())


def test_shared_surface_primitives_work_on_real_page(tmp_path):
    _run_surface_smoke(tmp_path, use_frame=False)


def test_shared_surface_primitives_work_on_real_frame(tmp_path):
    _run_surface_smoke(tmp_path, use_frame=True)


def test_shared_transition_fails_closed_when_real_frame_detaches(tmp_path):
    async def run():
        from playwright.async_api import async_playwright
        fixture=LocalFrameSurface(); outer_url=fixture.start(); browser_service=ApplicationBrowserService()
        try:
            async with async_playwright() as api:
                chromium=await api.chromium.launch(headless=True); context=await chromium.new_context(); page=await context.new_page()
                try:
                    await page.goto(outer_url, wait_until="domcontentloaded")
                    frame=next(item for item in page.frames if item != page.main_frame)
                    html=await browser_service._surface_content(frame)
                    waiting=asyncio.create_task(browser_service._wait_for_meaningful_transition(frame, browser_service._surface_url(frame), browser_service._page_fingerprint(html)))
                    await page.locator("#remove").click()
                    try:
                        await asyncio.wait_for(waiting, timeout=2)
                        raise AssertionError("detached application frame was accepted")
                    except RuntimeError as exc:
                        assert str(exc) == "APPLICATION_SURFACE_DETACHED"
                finally:
                    await context.close(); await chromium.close()
        finally:
            fixture.close()
    asyncio.run(run())

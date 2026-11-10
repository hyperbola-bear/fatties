"""Opt-in real-browser flow: FATTIES_UI_TESTS=1 pytest -q tests/test_ui.py.

Uses fake analysis, generated photos, a temporary database, and local storage.
It never contacts Anthropic, R2, or a production server.
"""

import os
import re
import socket
import threading
import time
from datetime import datetime

import pytest
import uvicorn
from test_app import PASSWORD, PLAN, item, settings
from test_upgrade import png

from fatties.analysis import FakeAnalyzer
from fatties.app import create_app, set_password
from fatties.store import Store
from fatties.targets import build_plan

pytestmark = pytest.mark.skipif(os.getenv("FATTIES_UI_TESTS") != "1", reason="opt-in browser verification")


@pytest.fixture
def live_app(tmp_path):
    cfg = settings(tmp_path)
    store = Store(cfg.db_path)
    store.ensure_people(cfg.people)
    today = datetime.now().astimezone().date()
    for person in store.people():
        set_password(store, person["name"], PASSWORD)
        plan = build_plan(**PLAN, start=today)
        store.save_plan(person["id"], today, plan["targets"], plan["plan"])
    store.add_meal(person_id=2, eaten_at=datetime.now().astimezone(), meal_type="lunch", title="Partner's lunch", note=None, items=[item()], model="fake")
    app = create_app(cfg, analyzer=FakeAnalyzer(), store=store)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}", store
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()


def test_mobile_log_track_plan_and_widget_setup(live_app):
    playwright = pytest.importorskip("playwright.sync_api")
    expect = playwright.expect
    url, store = live_app
    with playwright.sync_playwright() as p:
        browser = getattr(p, os.getenv("FATTIES_UI_BROWSER", "chromium")).launch()
        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.get_by_label("Name", exact=True).fill("Polar")
        page.get_by_label("Password", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="Log in", exact=True).click()
        expect(page.locator("#who")).to_contain_text("Polar")

        page.get_by_role("tab", name="Plan", exact=True).click()
        expect(page.locator("#target-people article")).to_have_count(2)
        expect(page.locator("#target-people article").nth(1)).to_contain_text("read-only")
        assert page.locator("#target-people article").nth(1).locator("input, select").count() == 0
        page.get_by_role("tab", name="Track", exact=True).click()
        for period in ["week", "month", "year"]:
            page.locator("#track-period").select_option(period)
            expect(page.locator("#track-people svg")).to_have_count(2)
            expect(page.locator("#track-status")).to_have_text("")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path="/tmp/fatties-track-year-review.png", full_page=True)
        page.locator("#track-period").select_option("month")
        page.locator("#track-date").fill("2024-02-29")
        expect(page.locator("#track-label")).to_contain_text("2024")
        page.locator("#track-next").click()
        expect(page.locator("#track-date")).to_have_value("2024-03-01")

        page.get_by_role("tab", name="Log", exact=True).click()
        page.locator("#photo").set_input_files({"name": "meal.png", "mimeType": "image/png", "buffer": png()})
        page.get_by_role("button", name="Estimate", exact=True).click()
        expect(page.locator("details.item")).to_have_count(3)
        first = page.locator("details.item").first
        first.locator("summary").click()
        protein = page.get_by_label("Chicken rice Protein exact grams", exact=True)
        protein.fill("40")
        page.get_by_label("Meal name", exact=True).click()
        expect(protein).to_have_value("40")
        slider = page.get_by_role("slider", name="Whole meal portion", exact=True)
        handle = slider.element_handle()
        slider.focus()
        slider.press("Home")
        expect(protein).to_have_value("20")
        slider.press("ArrowRight")
        expect(protein).to_have_value("22")
        assert handle.evaluate("node => node.isConnected")  # The active slider was not replaced.
        box = slider.bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.7, box["y"] + box["height"] / 2, steps=12)
        page.mouse.up()
        assert handle.evaluate("node => node.isConnected")
        assert float(slider.input_value()) > 1
        sugar = page.get_by_label("Chicken rice Sugar exact grams", exact=True)
        sugar.fill("1000")
        page.get_by_label("Meal name", exact=True).click()
        carbs = float(page.get_by_label("Chicken rice Carbs exact grams", exact=True).input_value())
        assert float(sugar.input_value()) <= carbs
        page.get_by_role("button", name="Reset to estimate", exact=True).click()
        expect(protein).to_have_value("7")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path="/tmp/fatties-log-review.png", full_page=True)

        page.get_by_role("button", name="Save for Polar", exact=True).click()
        expect(page.locator("#view-track")).to_be_visible()
        saved = store.latest_meal(1)
        assert saved and saved["has_photo"] and saved["totals"]["kcal"] == 597
        page.locator("#track-people").get_by_role("link", name=saved["title"], exact=True).click()
        expect(page.locator("#meal-detail h2")).to_have_text(saved["title"])
        page.wait_for_function("document.querySelector('.meal-photo')?.naturalWidth > 0")
        page.reload()
        expect(page.locator("#meal-detail h2")).to_have_text(saved["title"])

        page.get_by_role("tab", name="Widget", exact=True).click()
        page.get_by_role("button", name="Create widget connection", exact=True).click()
        expect(page.locator("#widget-code-box")).to_be_visible()
        code = page.locator("#widget-code").input_value()
        assert '"token"' in code and page.locator("#widget-code").evaluate("el => el.tagName") == "TEXTAREA"
        expect(page.locator("#widget-code-box")).to_be_in_viewport()
        page.get_by_role("button", name="Copy connection code", exact=True).click()
        expect(page.locator("#widget-copy-status")).to_have_text(re.compile(r"^(Copied\.|Couldn't copy automatically\.)"))
        assert len(store.widget_connections(1)) == 1
        page.get_by_role("tab", name="Plan", exact=True).click()
        expect(page.locator("#widget-code")).to_have_value("")
        page.get_by_role("tab", name="Widget", exact=True).click()
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Disconnect my widgets", exact=True).click()
        expect(page.locator("#widget-status")).to_contain_text("Disconnected")
        assert not store.widget_connections(1)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path="/tmp/fatties-widget-review.png", full_page=True)

        page.get_by_role("tab", name="Plan", exact=True).click()
        page.get_by_role("button", name="Log out", exact=True).click()
        expect(page.locator("#view-login")).to_be_visible()
        page.get_by_label("Name", exact=True).fill("Partner")
        page.get_by_label("Password", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="Log in", exact=True).click()
        expect(page.locator("#who")).to_contain_text("Partner")
        page.get_by_role("tab", name="Plan", exact=True).click()
        expect(page.locator("#target-people article").first).to_contain_text("read-only")
        assert not errors, errors
        context.close()
        browser.close()

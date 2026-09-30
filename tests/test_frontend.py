"""Presentation regression checks; no changes to prediction or account behavior."""

from html.parser import HTMLParser

import httpx
import pytest
from starlette.requests import Request

from webapp.main import app
from webapp.plans import PLANS
from webapp.templates import templates


class Elements(HTMLParser):
    def __init__(self, source: str):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def find(self, tag: str):
        return [attrs for name, attrs in self.tags if name == tag]


@pytest.mark.parametrize("prefix,locale", [("", "en"), ("/fr", "fr")])
async def test_landing_localization_and_preview_semantics(prefix, locale):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"{prefix}/")
    assert response.status_code == 200
    elements = Elements(response.text)
    assert elements.find("html")[0]["lang"] == locale
    tabs = [e for e in elements.find("button") if e.get("role") == "tab"]
    assert len(tabs) == 3
    assert sum(t["aria-selected"] == "true" for t in tabs) == 1
    ids = [a["id"] for _, a in elements.tags if "id" in a]
    assert len(ids) == len(set(ids))
    assert all(t["aria-controls"] in ids for t in tabs)
    assert f'href="{prefix}/register"' in response.text
    assert (
        "Exemple illustratif" in response.text
        if locale == "fr"
        else "Illustrative example" in response.text
    )
    for plan in PLANS.values():
        assert f"{plan.price_fcfa:,}" in response.text


@pytest.mark.parametrize("prefix", ["", "/fr"])
async def test_auth_form_keeps_fields_and_has_accessible_labels(prefix):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"{prefix}/login")
    elements = Elements(response.text)
    form = elements.find("form")[0]
    assert form["action"] == f"{prefix}/login"
    assert form["method"] == "post"
    inputs = {field["name"]: field for field in elements.find("input")}
    assert {"login", "password", "next"} <= inputs.keys()
    labels = {label.get("for") for label in elements.find("label")}
    assert inputs["login"]["id"] in labels
    assert inputs["password"]["id"] in labels
    assert inputs["login"]["autocomplete"] == "email"
    assert inputs["password"]["type"] == "password"
    toggle = next(b for b in elements.find("button") if b.get("id") == "mobile-nav-toggle")
    assert toggle["aria-controls"] == "mobile-nav"
    assert toggle["aria-expanded"] == "false"


@pytest.mark.parametrize(
    "asset",
    ["css/design.css", "js/design.js", "fonts/manrope-latin.woff2", "js/vendor/htmx.min.js"],
)
async def test_design_assets_are_served_locally(asset):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/static/{asset}")
    assert response.status_code == 200
    assert len(response.content) > 100


def test_fixture_redesign_preserves_day_controls_and_prediction_payload():
    request = Request({"type": "http", "path": "/simulate", "headers": [], "query_string": b""})
    request.state.locale = "en"
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    fixtures = {day: [] for day in days}
    fixtures["wednesday"] = [
        {
            "name": "Premier League",
            "flag_code": "gb-eng",
            "fixtures": [
                {
                    "league_id": 1,
                    "kickoff_utc": 1790794800,
                    "time_label": "20:00",
                    "home": {"id": 9, "name": "Arsenal"},
                    "away": {"id": 1, "name": "Aston Villa"},
                }
            ],
        }
    ]
    rendered = templates.env.get_template("_daily_fixtures.html").render(
        request=request,
        fixture_days=fixtures,
        current_weekday="wednesday",
        week_day_numbers=dict(zip(days, range(1, 8), strict=True)),
    )
    elements = Elements(rendered)
    radios = [field for field in elements.find("input") if field.get("type") == "radio"]
    assert len(radios) == 7
    assert [r["id"] for r in radios if "checked" in r] == ["fixture-day-wednesday"]
    values = {
        field["name"]: field.get("value")
        for field in elements.find("input")
        if field.get("type") == "hidden"
    }
    assert values == {
        "league_id": "1",
        "fixture_kickoff": "1790794800",
        "home": "Arsenal",
        "away": "Aston Villa",
    }
    assert elements.find("form")[0]["action"] == "/simulate"
    assert "peer-checked/wednesday:block" in rendered

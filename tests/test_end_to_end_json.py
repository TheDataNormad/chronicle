"""End-to-end tests of the JSON/API path: fetch -> parse -> normalize ->
store -> diff, against a real local server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chronicle import Scrape
from chronicle.exceptions import ConfigurationError
from tests._server import Route, json_route, serve

NO_ROBOTS = Route("", status=404)


def products(*items):
    return {"products": [dict(i) for i in items]}


P1 = {"id": 1, "title": "Mug", "sku": "00123", "price": "9.99", "in_stock": True}
P2 = {"id": 2, "title": "Cup", "sku": "00456", "price": "7.50", "in_stock": False}
P3 = {"id": 3, "title": "Jug", "sku": "00789", "price": "12.00", "in_stock": True}


def _scrape(base, root, path="/products.json", **kw):
    kw.setdefault("rate_limit", 1000.0)
    return Scrape(url=f"{base}{path}", storage_root=str(root), **kw).run()


def test_json_is_detected_automatically(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/products.json": json_route(products(P1, P2))}
    with serve(routes) as (base, _):
        r = _scrape(base, tmp_path)
    df = r.to_dataframe()
    assert r.source_kind == "json" and len(df) == 2
    assert df["sku"].tolist() == ["00123", "00456"]      # leading zeros survive
    assert df["in_stock"].tolist() == [True, False]      # real booleans
    assert df["id"].tolist() == [1, 2]                   # real ints


def test_json_strings_are_not_reinterpreted(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/p.json": json_route(products(
        {"id": 1, "flag": "yes", "code": "1"}, {"id": 2, "flag": "no", "code": "0"}))}
    with serve(routes) as (base, _):
        df = _scrape(base, tmp_path, "/p.json").to_dataframe()
    assert df["flag"].tolist() == ["yes", "no"]          # not turned into bools
    assert df["code"].tolist() == ["1", "0"]


def test_fields_selects_and_renames(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/products.json": json_route(products(
        {**P1, "variants": [{"price": "9.99"}]}))}
    with serve(routes) as (base, _):
        r = _scrape(base, tmp_path, fields={"name": "title", "price": "variants.0.price"})
    assert list(r.to_dataframe().columns) == ["name", "price"]


def test_pagination_stops_when_the_api_runs_out(tmp_path):
    routes = {
        "/robots.txt": NO_ROBOTS,
        "/products.json": json_route(products(P1)),
        "/products.json?page=2": json_route(products(P2)),
        "/products.json?page=3": json_route(products()),      # past the end
    }
    with serve(routes) as (base, hits):
        r = _scrape(base, tmp_path, pages=10)
    assert len(r.to_dataframe()) == 2
    assert r.pages_failed == 0 and r.pages_requested == 3     # stopped at the empty page
    assert hits["/products.json?page=4"] == 0


def test_second_run_reports_changes_with_key(tmp_path):
    state = {"n": 0}

    def catalogue(_hit):
        state["n"] += 1
        if state["n"] == 1:
            return json_route(products(P1, P2))
        return json_route(products({**P1, "price": "11.99"}, P3))   # P2 gone, P3 new

    routes = {"/robots.txt": NO_ROBOTS, "/products.json": catalogue}
    with serve(routes) as (base, _):
        _scrape(base, tmp_path, key="id")
        second = _scrape(base, tmp_path, key="id")
    c = second.changes
    assert (c.rows_added, c.rows_removed, c.rows_changed) == (1, 1, 1)
    assert c.changed[0].column == "price" and c.changed[0].new == "11.99"


def test_raw_response_is_stored_as_json(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/products.json": json_route(products(P1))}
    with serve(routes) as (base, _):
        r = _scrape(base, tmp_path)
    run = Path(tmp_path) / "projects" / r.manifest["project_id"] / "runs" / r.manifest["run_id"]
    assert json.loads((run / "raw" / "page_1.json").read_text())["products"][0]["id"] == 1


def test_api_keys_never_reach_disk(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/products.json": json_route(products(P1))}
    with serve(routes) as (base, _):
        r = _scrape(base, tmp_path, "/products.json?api_key=SUPERSECRET&limit=5")
        on_disk = "".join(
            p.read_text(errors="ignore") for p in Path(tmp_path).rglob("*.json")
        )
        index = (Path(tmp_path) / "index.db").read_bytes()
    assert "SUPERSECRET" not in on_disk and b"SUPERSECRET" not in index
    assert "api_key=REDACTED" in r.manifest["url"]


def test_secret_header_is_sent_but_never_stored(tmp_path):
    seen = {}

    def guarded(_hit):
        return json_route(products(P1))

    routes = {"/robots.txt": NO_ROBOTS, "/products.json": guarded}
    with serve(routes) as (base, _):
        r = _scrape(base, tmp_path, headers={"X-Api-Key": "HEADERSECRET"})
    assert not seen
    on_disk = "".join(p.read_text(errors="ignore") for p in Path(tmp_path).rglob("*.json"))
    assert "HEADERSECRET" not in on_disk
    assert r.manifest["config"]["source"] == "auto"


def test_rotating_an_api_key_keeps_the_same_project(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/p.json": json_route(products(P1))}
    with serve(routes) as (base, _):
        a = _scrape(base, tmp_path, "/p.json?api_key=AAA")
        b = _scrape(base, tmp_path, "/p.json?api_key=BBB")
    assert a.manifest["project_id"] == b.manifest["project_id"]
    assert b.previous_run_id == a.manifest["run_id"]


# ------------------------------------------------------------ errors


def test_selectors_with_json_response_is_a_loud_configuration_error(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/products.json": json_route(products(P1))}
    with serve(routes) as (base, _), pytest.raises(ConfigurationError, match="JSON"):
        _scrape(base, tmp_path, selectors={"_container": "div", "a": "span"})


def test_records_path_with_html_response_is_a_loud_error(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/page": Route("<html><body>hi</body></html>")}
    with serve(routes) as (base, _), pytest.raises(ConfigurationError, match="HTML"):
        _scrape(base, tmp_path, "/page", records_path="products")


def test_ambiguous_json_is_reported_as_a_failed_page_not_guessed(tmp_path):
    data = {"users": [{"id": 1}], "orders": [{"id": 9}]}
    routes = {"/robots.txt": NO_ROBOTS, "/x.json": json_route(data)}
    with serve(routes) as (base, _):
        r = _scrape(base, tmp_path, "/x.json")
    assert r.pages_failed == 1 and "records_path" in r.errors[0]
    assert r.to_dataframe().empty


def test_empty_html_page_is_a_failed_page_not_a_crash(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/e": Route("   ")}
    with serve(routes) as (base, _):
        r = _scrape(base, tmp_path, "/e", selectors={"_container": "div", "a": "span"})
    assert r.pages_failed == 1 and r.pages_succeeded == 0
    assert "ParseError" in r.errors[0]


def test_robots_disallow_is_reported_as_a_failed_page(tmp_path):
    robots = Route("User-agent: *\nDisallow: /\n", content_type="text/plain")
    routes = {"/robots.txt": robots, "/products.json": json_route(products(P1))}
    with serve(routes) as (base, hits):
        r = _scrape(base, tmp_path)
    assert r.pages_failed == 1 and "robots.txt" in r.errors[0]
    assert hits["/products.json"] == 0


def test_key_must_be_a_known_column():
    with pytest.raises(ConfigurationError, match="key column"):
        Scrape("http://x/", fields={"a": "a"}, key="zzz")


def test_scrape_can_run_twice(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/products.json": json_route(products(P1))}
    with serve(routes) as (base, _):
        s = Scrape(url=f"{base}/products.json", storage_root=str(tmp_path), rate_limit=1000.0)
        assert len(s.run().to_dataframe()) == 1
        assert len(s.run().to_dataframe()) == 1


def test_unreadable_previous_run_is_reported_not_swallowed(tmp_path):
    routes = {"/robots.txt": NO_ROBOTS, "/products.json": json_route(products(P1))}
    with serve(routes) as (base, _):
        first = _scrape(base, tmp_path)
        run = (Path(tmp_path) / "projects" / first.manifest["project_id"]
               / "runs" / first.manifest["run_id"])
        (run / "manifest.json").unlink()                       # corrupt it
        second = _scrape(base, tmp_path)
    assert second.changes is None
    assert any("unavailable" in e for e in second.errors)


def test_mixed_na_column_run_is_saved_end_to_end(tmp_path):
    """The bug that used to lose a whole run: one 'N/A' in a numeric column."""
    html = ("<html><body>" + "".join(
        f'<div class="r"><span class="n">{n}</span><span class="v">{v}</span></div>'
        for n, v in [("a", "10"), ("b", "20"), ("c", "N/A")]) + "</body></html>")
    routes = {"/robots.txt": NO_ROBOTS, "/t": Route(html)}
    with serve(routes) as (base, _):
        r = _scrape(base, tmp_path, "/t",
                    selectors={"_container": "div.r", "n": "span.n", "v": "span.v"})
    assert r.manifest is not None and r.manifest["row_count"] == 3
    assert r.to_dataframe()["v"].isna().tolist() == [False, False, True]


def test_json_with_arrays_and_objects_is_saved_not_crashed(tmp_path):
    """Regression (found by the audit): list cells crashed profiling."""
    data = {"products": [
        {"id": 1, "title": "Mug", "tags": ["a", "b"], "variants": [{"price": "9.99"}]},
        {"id": 2, "title": "Cup", "tags": ["c"], "variants": [{"price": "7.50"}]},
    ]}
    routes = {"/robots.txt": NO_ROBOTS, "/products.json": json_route(data)}
    with serve(routes) as (base, _):
        first = _scrape(base, tmp_path, key="id")
        second = _scrape(base, tmp_path, key="id")
    assert first.manifest is not None and len(first.to_dataframe()) == 2
    assert second.changes is not None and not second.changes.has_changes

"""HTTP traces, site-file fetching and Firecrawl raw-HTML scraping (all offline)."""

import gzip
import json

import httpx
import pytest
import respx

from app.research.url_policy import UrlRejected
from app.seo.technical.site_files import fetch_site_file
from app.tools.firecrawl_tool import FIRECRAWL_SCRAPE_URL, FirecrawlTool
from tests.seo_support import FakeSite, policy


async def test_trace_records_every_hop_status_and_allow_listed_headers() -> None:
    site = FakeSite()
    site.redirect("http://example.com/old", "https://example.com/old", 301)
    site.redirect("https://example.com/old", "/new", 302)
    site.add("https://example.com/new", x_robots_tag="noindex", set_cookie="secret=1", link='</new>; rel="canonical"')
    async with site.client() as client:
        trace = await policy().trace_http("http://example.com/old", client, ["example.com"])
    assert [(h.url, h.status) for h in trace.hops] == [
        ("http://example.com/old", 301), ("https://example.com/old", 302), ("https://example.com/new", 200)]
    assert trace.redirect_count == 2 and trace.final_status == 200
    headers = dict(trace.final_headers)
    assert headers["x-robots-tag"] == "noindex" and "link" in headers
    assert "set-cookie" not in headers  # only allow-listed headers are kept


async def test_head_not_allowed_falls_back_to_get() -> None:
    site = FakeSite(head_not_allowed={"https://example.com/"})
    site.add("https://example.com/", body="<html></html>")
    async with site.client() as client:
        trace = await policy().trace_http("https://example.com/", client, ["example.com"])
    assert trace.final_status == 200 and trace.hops[0].method == "GET"
    assert [m for m, _ in site.requests] == ["HEAD", "GET"]


async def test_redirects_off_site_or_to_private_hosts_are_refused() -> None:
    site = FakeSite()
    site.redirect("https://example.com/out", "https://other.org/")
    site.redirect("https://example.com/in", "https://db.internal/")
    async with site.client() as client:
        with pytest.raises(UrlRejected) as off:
            await policy().trace_http("https://example.com/out", client, ["example.com"])
        with pytest.raises(UrlRejected) as private:
            await policy().trace_http("https://example.com/in", client)
    assert off.value.reason == "redirect_blocked" and private.value.reason == "redirect_blocked"
    assert ("HEAD", "https://other.org/") not in site.requests  # never requested
    assert ("HEAD", "https://db.internal/") not in site.requests


async def test_redirect_loop_stops_at_the_hop_limit() -> None:
    site = FakeSite()
    site.redirect("https://example.com/a", "https://example.com/b")
    site.redirect("https://example.com/b", "https://example.com/a")
    async with site.client() as client:
        with pytest.raises(UrlRejected) as loop:
            await policy(max_redirects=3).trace_http("https://example.com/a", client)
    assert loop.value.reason == "too_many_redirects" and len(site.requests) == 4


async def test_trace_redirects_keeps_its_previous_contract() -> None:
    site = FakeSite()
    site.redirect("https://example.com/a", "https://example.com/b")
    site.add("https://example.com/b")
    async with site.client() as client:
        trace = await policy().trace_redirects("https://example.com/a", client)
    assert trace.final_url == "https://example.com/b"
    assert trace.hops == ("https://example.com/a", "https://example.com/b")


async def test_site_file_fetch_follows_safe_redirects_and_caps_size() -> None:
    site = FakeSite()
    site.redirect("https://example.com/robots.txt", "https://www.example.com/robots.txt")
    site.add("https://www.example.com/robots.txt", body="User-agent: *\nDisallow:\n", content_type="text/plain")
    site.add("https://example.com/big.xml", body="x" * 5000)
    async with site.client() as client:
        robots = await fetch_site_file("https://example.com/robots.txt", policy=policy(), client=client,
                                       allowed_domains=["example.com"], max_bytes=1000)
        big = await fetch_site_file("https://example.com/big.xml", policy=policy(), client=client,
                                    allowed_domains=["example.com"], max_bytes=1000)
        missing = await fetch_site_file("https://example.com/nope.xml", policy=policy(), client=client,
                                        allowed_domains=["example.com"], max_bytes=1000)
    assert robots.outcome == "ok" and robots.redirects == ["https://example.com/robots.txt"]
    assert robots.text.startswith("User-agent")
    assert big.outcome == "too_large" and big.text is None
    assert missing.outcome == "client_error" and missing.status == 404


async def test_site_file_blocks_private_targets_and_decompresses_gzip() -> None:
    site = FakeSite()
    site.add("https://example.com/sitemap.xml.gz", body=gzip.compress(b"<urlset></urlset>"),
             content_type="application/gzip")
    async with site.client() as client:
        blocked = await fetch_site_file("https://cache.internal/robots.txt", policy=policy(), client=client,
                                        allowed_domains=["cache.internal"], max_bytes=1000)
        gz = await fetch_site_file("https://example.com/sitemap.xml.gz", policy=policy(), client=client,
                                   allowed_domains=["example.com"], max_bytes=1000)
    assert blocked.outcome == "blocked" and site.requests == [("GET", "https://example.com/sitemap.xml.gz")]
    assert gz.outcome == "ok" and gz.text == "<urlset></urlset>"


async def test_gzip_bomb_is_rejected() -> None:
    site = FakeSite()
    site.add("https://example.com/s.xml.gz", body=gzip.compress(b"0" * 200_000))
    async with site.client() as client:
        result = await fetch_site_file("https://example.com/s.xml.gz", policy=policy(), client=client,
                                       allowed_domains=["example.com"], max_bytes=10_000)
    assert result.outcome == "too_large"


@respx.mock
async def test_firecrawl_seo_scrape_requests_raw_html_and_links(keyed_settings, fake_sleep) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(200, json={
        "success": True,
        "data": {"rawHtml": "<html><head><title>T</title></head></html>", "links": ["https://example.com/a", 5],
                 "markdown": "# ignored", "metadata": {"statusCode": 200, "url": "https://example.com/final"}},
    }))
    scrape = await FirecrawlTool(keyed_settings, sleep=fake_sleep).scrape_for_seo(
        "https://example.com/", max_html_chars=20)
    body = json.loads(route.calls.last.request.content)
    assert body["formats"] == ["rawHtml", "links"] and body["onlyMainContent"] is False
    assert scrape.raw_html == "<html><head><title>T"[:20] and scrape.truncated and scrape.original_length > 20
    assert scrape.links == ["https://example.com/a"]
    assert scrape.status_code == 200 and scrape.provider_final_url == "https://example.com/final"


@respx.mock
async def test_markdown_scrape_is_unchanged_by_the_seo_method(keyed_settings, fake_sleep) -> None:
    route = respx.post(FIRECRAWL_SCRAPE_URL).mock(return_value=httpx.Response(200, json={
        "success": True, "data": {"markdown": "# Title\n\nBody"}}))
    await FirecrawlTool(keyed_settings, sleep=fake_sleep).scrape_url("https://example.com/")
    body = json.loads(route.calls.last.request.content)
    assert body["formats"] == ["markdown"] and body["onlyMainContent"] is True


async def test_firecrawl_seo_scrape_refuses_private_urls(keyed_settings) -> None:
    from app.tools.errors import ProviderRequestError

    with pytest.raises(ProviderRequestError):
        await FirecrawlTool(keyed_settings).scrape_for_seo("http://127.0.0.1/admin", max_html_chars=100)

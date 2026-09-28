# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.63.0", "pytest==8.4.2"]
# ///

"""Check the built Dev Notes in a real browser, including future posts.

Run with `uv run --locked --script tests/test_dev_notes_layout.py` after building.
Use --install-browser once to install Chromium, WebKit, and their dependencies.
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Thread
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright
import pytest


ROOT = Path(__file__).resolve().parents[1]
POSTS = sorted((ROOT / "docs/dev-notes/posts").glob("*.md"))
VIDEO_POSTS = [post for post in POSTS if "<video" in post.read_text()]
VIEWPORTS = [(1366, 768), (1440, 900), (768, 1024), (390, 844), (320, 740)]
SCREENSHOTS = ROOT / ".cache/dev-notes-layout"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="session")
def site_url():
    assert (ROOT / "site/dev-notes/index.html").is_file(), "Run scripts/build-docs.sh first"
    assert POSTS, "No Dev Notes were discovered"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "site")))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture(scope="session", params=["chromium", "webkit"])
def browser(request):
    with sync_playwright() as playwright:
        options = {}
        if request.param == "chromium" and os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        browser = getattr(playwright, request.param).launch(**options)
        yield browser
        browser.close()


@pytest.fixture(params=VIEWPORTS, ids=lambda size: f"{size[0]}x{size[1]}")
def viewport(request):
    return {"width": request.param[0], "height": request.param[1]}


@pytest.fixture(params=["default", "slate"])
def page(request, browser, viewport):
    page = browser.new_page(
        viewport=viewport, reduced_motion="reduce",
        color_scheme="dark" if request.param == "slate" else "light",
    )
    # GitHub avatars and third-party videos must not make layout checks depend
    # on external services. All site CSS, fonts, hero images and scripts load.
    page.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname == "127.0.0.1" else route.abort())
    page.add_init_script(f"window.addEventListener('DOMContentLoaded', () => document.body.dataset.mdColorScheme = '{request.param}')")
    yield page
    if not page.is_closed() and page.url != "about:blank":
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9_.-]", "_", request.node.name)
        page.screenshot(path=str(SCREENSHOTS / f"{name}.png"))
    page.close()


def open_page(page, url):
    response = page.goto(url, wait_until="networkidle")
    assert response and response.ok, f"Missing built page: {url}"
    page.evaluate("document.fonts.ready")
    expected_theme = "slate" if page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches") else "default"
    assert page.locator("body").get_attribute("data-md-color-scheme") == expected_theme


def assert_images_loaded(images):
    images.evaluate_all("images => images.forEach(image => image.loading = 'eager')")
    for image in images.all():
        image.evaluate("image => image.decode()")
        assert image.evaluate("image => image.naturalWidth > 0"), image.get_attribute("src")


def assert_no_horizontal_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.url


def assert_footer_navigation_visible(page):
    links = page.locator(".md-footer__link")
    boxes = []
    for link in links.all():
        box = link.bounding_box()
        boxes.append(box)
        for selector in (".md-footer__direction", ".md-footer__title"):
            content = link.locator(selector).bounding_box()
            assert content, "Footer labels should be visible"
            assert content["y"] >= box["y"] - 1, "Footer label is clipped above its link"
            assert content["y"] + content["height"] <= box["y"] + box["height"] + 1, (
                "Footer link must grow to contain its title"
            )
    if len(boxes) == 2:
        previous, next_page = boxes
        assert (
            previous["x"] + previous["width"] <= next_page["x"] + 1
            or previous["y"] + previous["height"] <= next_page["y"] + 1
        ), "Previous and next links must not overlap"


def assert_index_proportions(page, viewport):
    assert_no_horizontal_overflow(page)
    assert_footer_navigation_visible(page)
    cards = page.locator(".dev-note-card")
    assert cards.count() == len(POSTS)
    assert_images_loaded(cards.locator(".dev-note-card__visual-image"))
    for visual in cards.locator(".dev-note-card__visual").all():
        images = visual.locator("img:visible")
        if visual.locator("img").count():
            assert images.count() == 1, "Only one theme variant should be visible"
            assert images.first.evaluate("image => getComputedStyle(image).objectFit === 'contain'")
    for visual in page.locator(".dev-note-card--recent .dev-note-card__visual").all():
        assert visual.bounding_box()["height"] <= 120, "Recent images should stay thumbnails"
    if viewport["width"] >= 1024:
        featured = page.locator(".dev-note-card--featured").bounding_box()
        assert featured["y"] + featured["height"] < viewport["height"], "Featured note pushes the list below the fold"
        if page.locator(".dev-note-card--recent").count():
            title = page.locator(".dev-note-card--recent h3").first.bounding_box()
            assert title["y"] + title["height"] < viewport["height"], "First recent title should be visible without scrolling"


def test_index_reading_proportions(page, site_url, viewport):
    open_page(page, f"{site_url}/dev-notes/")
    assert_index_proportions(page, viewport)


def test_featured_image_also_works_as_a_thumbnail(page, site_url, viewport):
    open_page(page, f"{site_url}/dev-notes/")
    if page.locator(".dev-note-card--recent").count() == 0:
        pytest.skip("Only one published note")
    page.evaluate("""() => {
        const featured = document.querySelector('.dev-note-card--featured');
        const recent = document.querySelector('.dev-note-card--recent');
        const section = featured.parentElement;
        featured.classList.replace('dev-note-card--featured', 'dev-note-card--recent');
        recent.classList.replace('dev-note-card--recent', 'dev-note-card--featured');
        document.querySelector('.dev-notes-recent-list').prepend(featured);
        section.append(recent);
    }""")
    assert_index_proportions(page, viewport)


def test_index_filters_combine_restore_and_share(page, site_url):
    open_page(page, f"{site_url}/dev-notes/")
    records = [
        {
            "title": card.locator("h3").inner_text(),
            "category": card.get_attribute("data-category"),
            "authors": json.loads(card.get_attribute("data-authors")),
        }
        for card in page.locator(".dev-note-card").all()
    ]
    category = page.get_by_role("combobox", name="Category", exact=True)
    author = page.get_by_role("combobox", name="Author", exact=True)
    titles = page.locator(".dev-note-card:visible h3")
    clear = page.get_by_role("button", name="Clear filters", exact=True)
    selected_author = records[0]["authors"][0]
    author.select_option(selected_author)
    author_matches = [record["title"] for record in records if selected_author in record["authors"]]
    expect(titles).to_have_text(author_matches)
    assert parse_qs(urlparse(page.url).query)["author"] == [selected_author]

    for value in ["announcements", "research", "case-studies", "examples"]:
        category.select_option(value)
        matches = [
            record["title"] for record in records
            if selected_author in record["authors"] and record["category"] == value
        ]
        expect(titles).to_have_text(matches)
        if matches:
            expect(page.locator(".dev-notes-filter-empty")).to_be_hidden()
        else:
            expect(page.locator(".dev-notes-filter-empty")).to_be_visible()
        assert_no_horizontal_overflow(page)

    page.reload(wait_until="networkidle")
    expect(category).to_have_value("examples")
    expect(author).to_have_value(selected_author)
    expect(titles).to_have_text(matches)
    clear.click()
    expect(titles).to_have_text([record["title"] for record in records])
    expect(category).to_be_focused()
    expect(page.locator(".dev-note-card--featured h3")).to_have_text(records[0]["title"])
    page.go_back()
    expect(category).to_have_value("examples")
    expect(author).to_have_value(selected_author)
    expect(titles).to_have_text(matches)
    page.go_forward()
    expect(titles).to_have_text([record["title"] for record in records])

    # Match a different author and verify their newest note is promoted.
    selected_author = records[-1]["authors"][0]
    author.select_option(selected_author)
    matches = [record["title"] for record in records if selected_author in record["authors"]]
    expect(titles).to_have_text(matches)
    expect(page.locator(".dev-note-card--featured h3")).to_have_text(matches[0])
    clear.click()
    open_page(page, f"{site_url}/dev-notes/?category=invalid&author=unknown&source=check")
    expect(titles).to_have_text([record["title"] for record in records])
    assert parse_qs(urlparse(page.url).query) == {"source": ["check"]}


def test_article_bylines_open_filtered_notes(page, site_url):
    post_url = f"{site_url}/dev-notes/posts/{POSTS[0].stem}/"
    open_page(page, post_url)
    title = page.locator(".dev-note-header h1").inner_text()
    page.locator(".dev-note-byline__author").first.click()
    expect(page.locator(".dev-notes-filters")).to_be_visible()
    assert parse_qs(urlparse(page.url).query)["author"]
    expect(page.locator(".dev-note-card:visible h3").filter(has_text=title)).to_have_count(1)
    open_page(page, post_url)
    page.locator(".dev-note-byline__label a").click()
    expect(page.locator(".dev-notes-filters")).to_be_visible()
    assert parse_qs(urlparse(page.url).query)["category"]
    expect(page.locator(".dev-note-card:visible h3").filter(has_text=title)).to_have_count(1)


def test_index_remains_readable_without_javascript(browser, site_url):
    page = browser.new_page(java_script_enabled=False)
    page.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname == "127.0.0.1" else route.abort())
    try:
        response = page.goto(f"{site_url}/dev-notes/?category=research")
        assert response.ok
        expect(page.locator(".dev-notes-filters")).to_be_hidden()
        expect(page.locator(".dev-note-card:visible")).to_have_count(len(POSTS))
        page.locator(".dev-note-card__link").first.click()
        expect(page.locator(".dev-note-header h1")).to_be_visible()
    finally:
        page.close()


@pytest.mark.parametrize("post", POSTS, ids=lambda post: post.stem)
def test_post_reading_proportions(page, site_url, viewport, post):
    open_page(page, f"{site_url}/dev-notes/posts/{post.stem}/")
    article = page.locator(".md-content__inner")
    header = article.locator(":scope > .dev-note-header")
    assert header.count() == 1
    assert article.locator("h1").count() == 1
    assert article.locator(":scope > .dev-note-byline").count() == 1
    assert_no_horizontal_overflow(page)
    assert_footer_navigation_visible(page)
    metrics = article.evaluate("""article => {
        const style = getComputedStyle(article);
        const font = parseFloat(style.fontSize);
        // Measure ch from the actual font; fallback metrics differ by browser.
        const context = document.createElement('canvas').getContext('2d');
        context.font = `${style.fontSize} ${style.fontFamily}`;
        return {font, lineHeight: parseFloat(style.lineHeight) / font,
                measure: article.getBoundingClientRect().width / context.measureText('0').width};
    }""")
    assert metrics["font"] >= 16, "Body text must be readable on a phone"
    assert 1.5 <= metrics["lineHeight"] <= 1.9
    assert metrics["measure"] <= 75, "Paragraph lines should stay within the 74ch reading measure (allowing rounding)"
    title = header.locator("h1")
    assert title.evaluate("title => parseFloat(getComputedStyle(title).fontSize)") <= 3.2 * metrics["font"]
    hero = header.locator(":scope > .dev-note-figure--hero")
    frontmatter = post.read_text().split("\n---\n", 1)[0]
    has_hero = bool(re.search(r"(?m)^hero_image:", frontmatter))
    assert hero.count() == int(has_hero), "Hero should be generated once from front matter"
    if not has_hero:
        return
    assert_images_loaded(hero.locator("img"))
    assert hero.locator("img:visible").count() == 1
    image = hero.locator("img:visible")
    dimensions = image.bounding_box()
    frame = hero.bounding_box()
    opening = header.bounding_box()
    byline = article.locator(":scope > .dev-note-byline").bounding_box()
    column = article.bounding_box()
    image_center = dimensions["x"] + dimensions["width"] / 2
    column_center = column["x"] + column["width"] / 2
    assert abs(image_center - column_center) <= 1, "Hero should be centered in the reading column"
    assert dimensions["y"] >= frame["y"] - 1
    assert dimensions["y"] + dimensions["height"] <= frame["y"] + frame["height"] + 1, (
        "Hero image must stay inside its figure"
    )
    assert frame["y"] + frame["height"] <= opening["y"] + opening["height"] + 1, (
        "Hero figure must stay inside the opening"
    )
    assert byline["y"] >= opening["y"] + opening["height"] + 8, (
        "Byline must leave a clear gap after the hero"
    )
    assert dimensions["y"] + dimensions["height"] < viewport["height"], "Title, subtitle, and full hero must fit without scrolling"
    assert dimensions["height"] > 0
    assert image.evaluate("image => getComputedStyle(image).objectFit === 'contain'"), "Hero must remain uncropped and undistorted"
    ratio = image.evaluate("image => image.naturalWidth / image.naturalHeight")
    column_width = article.bounding_box()["width"]
    assert dimensions["width"] <= column_width + 1
    # Check the actual contained artwork against the available opening space,
    # catching thumbnail-sized heroes without penalizing longer subtitles.
    available_height = header.evaluate("header => parseFloat(getComputedStyle(header).maxHeight)")
    available_height -= dimensions["y"] - header.bounding_box()["y"] + 16
    ideal_width = min(column_width, max(0, available_height) * ratio, 32 * metrics["font"] * ratio)
    artwork_width = min(dimensions["width"], dimensions["height"] * ratio)
    assert artwork_width >= ideal_width * 0.85, "Article hero should use the available opening space"
    assert header.evaluate("header => Boolean(header.nextElementSibling?.classList.contains('dev-note-byline'))"), "Opening should precede the byline"
    link = image.locator("xpath=..")
    assert link.get_attribute("href") == image.get_attribute("src"), "Readers need access to the full-size diagram"
    assert image.get_attribute("alt"), "Hero needs an authored text alternative"


def test_shared_documentation_navigation(page, site_url, viewport):
    open_page(page, f"{site_url}/documentation/openshell-agent-runner/reviews/")
    assert_no_horizontal_overflow(page)
    assert_footer_navigation_visible(page)
    for label in page.locator(".md-path__link .md-ellipsis").all():
        box = label.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"]
        assert label.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), (
            "Breadcrumb labels should wrap instead of clipping"
        )
    # A table can scroll horizontally without squeezing commands into fragments
    # or making the entire page scroll sideways.
    table = page.locator(".md-typeset__scrollwrap").first
    table.scroll_into_view_if_needed()
    if viewport["width"] <= 390:
        assert table.evaluate("e => e.scrollWidth > e.clientWidth")
        table.evaluate("e => e.scrollLeft = e.scrollWidth")
        assert table.evaluate("e => e.scrollLeft > 0"), "Wide tables must remain scrollable"
    if viewport["width"] < 1024:
        page.get_by_role("button", name="Open navigation", exact=True).click()
        close = page.locator(".openshell-drawer-close")
        assert close.is_visible(), "Touch navigation needs a visible close control"
        close.click()
        assert page.get_by_role("button", name="Open navigation", exact=True).evaluate(
            "e => e === document.activeElement"
        ), "Closing the drawer should restore focus"


@pytest.mark.parametrize("post", VIDEO_POSTS, ids=lambda post: post.stem)
def test_embedded_video_playback(page, site_url, post):
    open_page(page, f"{site_url}/dev-notes/posts/{post.stem}/")
    for video in page.locator("video").all():
        video.scroll_into_view_if_needed()
        video.evaluate("video => { video.muted = true; void video.play(); }")
        page.wait_for_function(
            "video => video.currentTime > 0.1 && video.videoWidth > 0",
            arg=video.element_handle(), timeout=10000,
        )
        video.evaluate("video => video.pause()")
        assert video.evaluate("video => video.paused && !video.error")


if __name__ == "__main__":
    if sys.argv[1:] == ["--install-browser"]:
        raise SystemExit(subprocess.call([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium", "webkit"]))
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))

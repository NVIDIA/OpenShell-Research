# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "render_dev_notes", Path(__file__).resolve().parents[1] / "scripts/render-dev-notes.py"
)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


@pytest.fixture
def post(tmp_path, monkeypatch):
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    path = tmp_path / "docs/dev-notes/posts/note.md"
    path.parent.mkdir(parents=True)
    (path.parent / "hero.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (path.parent / "dark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    frontmatter = (
        "title: A note\ndate: 2026-09-28\ndescription: Summary\nauthors:\n  - ada"
        "\ncategories:\n  - Research"
    )
    body = "An introduction with **Markdown**.\n\n## Evidence\n\nKeep this prose.\n"
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}")
    metadata, frontmatter, body = renderer.parse_frontmatter(path.read_text(), path)
    return {
        "path": path, "metadata": metadata, "frontmatter": frontmatter, "body": body,
        "authors": [{"name": "Ada", "github": "ada"}],
        "published": renderer.parse_date("2026-09-28", path),
    }


def test_new_post_gets_header_without_manual_markup_and_is_idempotent(post):
    original_body = post["body"]
    renderer.update_post_header(post)
    once = post["path"].read_text()
    assert once.endswith(original_body)
    assert once.count("# A note\n") == 1
    assert once.count(renderer.HEADER_START) == 1
    assert once.count(renderer.BYLINE_START) == 1
    _, _, post["body"] = renderer.parse_frontmatter(once, post["path"])
    renderer.update_post_header(post)
    assert post["path"].read_text() == once


def test_metadata_controls_order_and_escapes_subtitle_and_image_alt(post):
    post["metadata"].update(
        subtitle='Why <tools> matter & work.', hero_image="hero.svg",
        hero_image_dark="dark.svg", hero_image_alt='A "boundary" & <agent>',
    )
    result = renderer.render_post_header(post)
    assert result.index("# A note") < result.index("dev-note-deck")
    assert result.index("dev-note-deck") < result.index("<figure") < result.index(renderer.BYLINE_START)
    assert 'alt="A &quot;boundary&quot; &amp; &lt;agent&gt;"' in result
    assert 'Why &lt;tools&gt; matter &amp; work.' in result
    assert 'class="dev-note-image--dark" href="dark.svg"' in result
    assert 'class="dev-note-image--light" href="hero.svg"' in result


@pytest.mark.parametrize("metadata, error", [
    ({"hero_image": "hero.svg"}, "hero_image_alt"),
    ({"hero_image_dark": "dark.svg"}, "requires hero_image"),
    ({"hero_image": "missing.svg"}, "does not exist"),
    ({"hero_image": "../../../../outside.svg"}, "must stay inside"),
])
def test_invalid_hero_metadata_has_actionable_errors(post, metadata, error):
    post["metadata"].update(metadata)
    with pytest.raises(ValueError, match=error):
        renderer.render_post_header(post)


def test_metadata_change_replaces_header_without_changing_prose(post):
    renderer.update_post_header(post)
    _, _, post["body"] = renderer.parse_frontmatter(post["path"].read_text(), post["path"])
    post["metadata"]["subtitle"] = "A new subtitle"
    post["metadata"]["hero_image"] = "hero.svg"
    post["metadata"]["hero_image_alt"] = "A boundary"
    renderer.update_post_header(post)
    result = post["path"].read_text()
    assert result.count("<figure") == 1
    assert "A new subtitle" in result
    assert result.endswith("## Evidence\n\nKeep this prose.\n")


def test_partial_header_marker_is_rejected_without_writing(post):
    original = post["path"].read_text()
    post["body"] = renderer.HEADER_START + "\nBroken header\n"
    with pytest.raises(ValueError, match="missing generated section markers"):
        renderer.update_post_header(post)
    assert post["path"].read_text() == original


def test_legacy_opening_is_not_silently_duplicated(post):
    post["body"] = "# Old title\n\nProse to preserve.\n"
    with pytest.raises(ValueError, match="front matter"):
        renderer.update_post_header(post)


@pytest.mark.parametrize("category", ["Announcements", "Research", "Case Studies", "Examples"])
def test_fixed_category_is_consistent_on_cards_and_articles(post, category):
    post["metadata"]["categories"] = [category]
    assert f"<span>{category}</span>" in renderer.render_card_copy(post)
    assert f"<span>{category}</span>" in renderer.render_byline(post)
    assert f"Dev Note / {category}</span>" in renderer.render_card_visual(post)


@pytest.mark.parametrize("category_frontmatter", [
    "",
    "\ncategories:",
    "\ncategories: Research",
    "\ncategories:\n  - OpenShell",
    "\ncategories:\n  - research",
    "\ncategories:\n  - Research\n  - Examples",
    "\ncategories:\n  - Research\n  - Research",
    '\ncategories:\n  - ""',
])
def test_category_errors_leave_generated_content_untouched(post, monkeypatch, category_frontmatter):
    posts_dir = post["path"].parent
    invalid_path = posts_dir / "z-invalid.md"
    frontmatter = post["frontmatter"].split("\ncategories:", 1)[0] + category_frontmatter
    invalid_path.write_text(f"---\n{frontmatter}\n---\n\nUnchanged prose.\n")
    index_path = posts_dir.parent / "index.md"
    index_path.write_text(f"{renderer.POSTS_START}\nUnchanged index.\n{renderer.POSTS_END}")
    config_path = posts_dir.parent / "zensical.toml"
    config_path.write_text(f"{renderer.NAV_START}\nUnchanged navigation.\n{renderer.NAV_END}")
    monkeypatch.setattr(renderer, "POSTS_DIR", posts_dir)
    monkeypatch.setattr(renderer, "INDEX_PATH", index_path)
    monkeypatch.setattr(renderer, "CONFIG_PATH", config_path)
    monkeypatch.setattr(renderer, "load_authors", lambda: {"ada": {"name": "Ada", "github": "ada"}})
    originals = {path: path.read_bytes() for path in [post["path"], invalid_path, index_path, config_path]}

    with pytest.raises(ValueError, match="front matter field 'categories'"):
        renderer.main()

    assert {path: path.read_bytes() for path in originals} == originals

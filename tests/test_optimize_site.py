# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("optimize_site", ROOT / "scripts/optimize-site.py")
optimizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(optimizer)


def test_responsive_images_preserve_originals_and_preview_paths(tmp_path):
    site = tmp_path / "pr-preview/pr-123"
    asset = site / "assets/diagram.png"
    asset.parent.mkdir(parents=True)
    Image.new("RGB", (1600, 800), "green").save(asset)
    original = asset.read_bytes()
    (site / "index.html").write_text("<!doctype html><html><body></body></html>")
    page = site / "dev-notes/posts/example/index.html"
    page.parent.mkdir(parents=True)
    page.write_text('''<!doctype html><html><body><article>
      <a class="dev-note-image--dark" href="../../../assets/diagram.png">
        <img src="../../../assets/diagram.png" alt="Diagram" loading="eager" fetchpriority="high">
      </a>
      <img src="https://example.com/avatar.png" width="32" height="32">
      <video poster="../../../assets/diagram.png" preload="none"></video>
      <script>const value = "<hello>&world";</script>
    </article></body></html>''')
    assert optimizer.optimize_site(site) == 1
    soup = BeautifulSoup(page.read_text(), "html.parser")
    image = soup.select_one("a img")
    assert image["src"] == soup.a["href"]
    assert image["alt"] == "Diagram"
    assert image["loading"] == "lazy"
    assert image["fetchpriority"] == "high"
    assert (image["width"], image["height"]) == ("1600", "800")
    for entry in image["srcset"].split(", "):
        url, width = entry.split()
        variant = (page.parent / url).resolve()
        assert variant.is_relative_to(site)
        with Image.open(variant) as resized:
            assert resized.width == int(width[:-1])
            assert resized.width == resized.height * 2
    assert asset.read_bytes() == original
    assert "srcset" not in soup.select("img")[1].attrs
    assert (page.parent / soup.video["poster"]).is_file()
    assert soup.video["preload"] == "none"
    assert soup.script.string == 'const value = "<hello>&world";'
    before = page.read_bytes()
    assert optimizer.optimize_site(site) == 0
    assert page.read_bytes() == before


def test_asset_resolution_stays_inside_site(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    page = site / "index.html"
    outside = tmp_path / "private.png"
    outside.write_bytes(b"not an image")
    (site / "linked.png").symlink_to(outside)
    for url in ("../private.png", "linked.png", "https://example.com/x.png", "//example.com/x.png", "data:image/png;base64,abc"):
        assert optimizer.local_asset(site, page, url) is None


def test_existing_picture_sources_and_animated_images_are_preserved(tmp_path):
    Image.new("RGB", (20, 10), "red").save(tmp_path / "still.png")
    Image.new("RGB", (20, 10), "red").save(
        tmp_path / "animated.webp", save_all=True,
        append_images=[Image.new("RGB", (20, 10), "blue")], duration=100, loop=0,
    )
    page = tmp_path / "index.html"
    page.write_text('''<picture><source srcset="custom.webp"><img src="still.png"></picture>
      <img src="animated.webp"><img src="still.png" srcset="custom.png 2x">''')
    assert optimizer.optimize_site(tmp_path) == 0
    soup = BeautifulSoup(page.read_text(), "html.parser")
    assert not soup.picture.img.has_attr("srcset")
    assert not soup.select("img")[1].has_attr("srcset")
    assert soup.select("img")[2]["srcset"] == "custom.png 2x"


def test_palette_transparency_survives_compression(tmp_path):
    asset = tmp_path / "transparent.png"
    original = Image.new("P", (20, 10), 0)
    original.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    original.info["transparency"] = 0
    original.putpixel((10, 5), 1)
    original.save(asset)
    width, height, variants = optimizer.image_variants(asset, tmp_path)
    assert (width, height) == (20, 10)
    with Image.open(variants[0][0]) as converted:
        assert converted.getpixel((0, 0))[3] == 0
        assert converted.getpixel((10, 5))[3] == 255


def test_svg_space_is_reserved_without_converting_vectors(tmp_path):
    asset = tmp_path / "diagram.svg"
    original = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="350 220 1060 420"></svg>'
    asset.write_text(original)
    page = tmp_path / "index.html"
    page.write_text('''<img src="diagram.svg"><img src="diagram.svg" width="530">
      <img src="diagram.svg" height="210"><img src="diagram.svg" width="100" height="100">''')
    optimizer.optimize_site(tmp_path)
    images = BeautifulSoup(page.read_text(), "html.parser").select("img")
    assert [(image["width"], image["height"]) for image in images] == [
        ("1060", "420"), ("530", "210"), ("530", "210"), ("100", "100"),
    ]
    assert all(not image.has_attr("srcset") for image in images)
    assert asset.read_text() == original
    assert not (tmp_path / "assets/responsive").exists()

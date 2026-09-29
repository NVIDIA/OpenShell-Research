#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Add responsive images to the built site without changing published sources."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (320, 640, 960, 1440, 1920)
THEME_CLASSES = {
    "dev-note-image--light", "dev-note-image--dark",
    "openshell-home-brand__light", "openshell-home-brand__dark",
}
FEATURED_SIZES = "(max-width: 44rem) min(100vw - 32px, 26rem), 640px"
RECENT_SIZES = "(max-width: 44rem) 5rem, 10rem"
ARTICLE_SIZES = "(max-width: 800px) calc(100vw - 32px), 900px"


def local_asset(site: Path, page: Path, url: str) -> Path | None:
    parsed = urlsplit(url)
    # Site-relative URLs work identically at /, a project prefix, and PR previews.
    if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("/"):
        return None
    asset = (page.parent / unquote(parsed.path)).resolve()
    if not asset.is_relative_to(site) or not asset.is_file():
        return None
    return asset


def image_variants(asset: Path, site: Path) -> tuple[int, int, list[tuple[Path, int]]] | None:
    if asset.is_relative_to(site / "assets" / "responsive"):
        return None
    if asset.suffix.lower() == ".svg":
        # Keep vectors intact; their viewBox supplies the ratio before download.
        view_box = ElementTree.parse(asset).getroot().get("viewBox")
        if view_box:
            _, _, width, height = map(float, view_box.replace(",", " ").split())
            return round(width), round(height), []
        return None
    if asset.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    with Image.open(asset) as original:
        if getattr(original, "is_animated", False):
            return None
        transparent = "A" in original.getbands() or "transparency" in original.info
        image = ImageOps.exif_transpose(original).convert("RGBA" if transparent else "RGB")
        width, height = image.size
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()[:16]
        output = site / "assets" / "responsive"
        output.mkdir(parents=True, exist_ok=True)
        variants = []
        for size in sorted({min(width, candidate) for candidate in WIDTHS}):
            target = output / f"{digest}-{size}.webp"
            if not target.exists():
                resized = image.resize((size, max(1, round(height * size / width))), Image.Resampling.LANCZOS)
                resized.save(target, "WEBP", quality=88, method=6)
            variants.append((target, size))
        return width, height, variants


def relative_url(asset: Path, page: Path) -> str:
    return quote(Path(os.path.relpath(asset, page.parent)).as_posix())


def optimize_site(site: Path) -> int:
    site = site.resolve()
    if not (site / "index.html").is_file():
        raise ValueError(f"Build the site before optimizing it: {site}")
    cache = {}
    count = 0
    for page in sorted(site.rglob("*.html")):
        soup = BeautifulSoup(page.read_text(), "html.parser")
        before = str(soup)
        for image in soup.select("img[src]"):
            classes = set(image.get("class", [])) | set(image.parent.get("class", []))
            image.attrs.setdefault("decoding", "async")
            # Native lazy loading leaves display:none theme variants unfetched,
            # including when a saved palette differs from the system preference.
            if classes & THEME_CLASSES:
                image["loading"] = "lazy"
            elif not image.has_attr("loading") and image.find_parent("article"):
                image["loading"] = "lazy"
            asset = local_asset(site, page, image["src"])
            if asset is None or image.has_attr("srcset") or image.find_parent("picture"):
                continue
            if asset not in cache:
                cache[asset] = image_variants(asset, site)
            if not cache[asset]:
                continue
            width, height, variants = cache[asset]
            # Preserve authored sizing while completing its intrinsic ratio.
            if not image.has_attr("width") and not image.has_attr("height"):
                image["width"], image["height"] = str(width), str(height)
            elif image.get("width", "").isdigit() and not image.has_attr("height"):
                image["height"] = str(round(int(image["width"]) * height / width))
            elif image.get("height", "").isdigit() and not image.has_attr("width"):
                image["width"] = str(round(int(image["height"]) * width / height))
            if not variants:
                continue
            image["srcset"] = ", ".join(f"{relative_url(path, page)} {size}w" for path, size in variants)
            card = image.find_parent(class_="dev-note-card")
            image["sizes"] = (
                FEATURED_SIZES if card and "dev-note-card--featured" in card.get("class", [])
                else RECENT_SIZES if card else ARTICLE_SIZES
            )
            if card:
                image["data-featured-sizes"] = FEATURED_SIZES
                image["data-recent-sizes"] = RECENT_SIZES
            count += 1
        for video in soup.select("video[poster]"):
            asset = local_asset(site, page, video["poster"])
            if asset is not None:
                if asset not in cache:
                    cache[asset] = image_variants(asset, site)
                if cache[asset] and cache[asset][2]:
                    variants = cache[asset][2]
                    poster = next((path for path, size in variants if size >= 960), variants[-1][0])
                    video["poster"] = relative_url(poster, page)
        after = str(soup)
        if after != before:
            page.write_text(after, encoding="utf-8")
    return count


if __name__ == "__main__":
    print(f"Optimized {optimize_site(ROOT / 'site')} image placements with responsive WebP variants.")

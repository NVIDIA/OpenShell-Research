---
title: Dev Notes authoring instructions
robots: noindex, follow
---

# Dev Notes authoring

Read `docs/development/index.md` before editing a note. Authors supply front
matter and prose; `python3 scripts/render-dev-notes.py` generates the title,
optional subtitle and hero, author byline, index cards, and navigation.

- Set `categories` to a list containing exactly one of `Announcements`,
  `Research`, `Case Studies`, or `Examples`. Use tags for technical topics.
  The renderer rejects missing, unknown, or multiple categories.
- Use `subtitle` for an optional short deck and `hero_image_alt` to describe a
  `hero_image`. `hero_image_dark` is optional. Do not copy header HTML from a post.
- Start new post bodies with the introduction, without a duplicate H1 or hero.
- Do not edit the generated `dev-note:header`, `dev-note:byline`, or
  `dev-notes:posts` marker regions.
- Keep typography and hero sizing in the shared Dev Notes stylesheet. Do not add
  per-post title or hero sizing rules, inline styles, or fixed aspect ratios.
- Keep detailed body figures readable; the hero links to its full-size image.
- Run the documented build and locked browser layout checks. They discover all
  notes automatically and cover both themes, desktop and phone widths, and a
  featured note moving into the recent list. Commit regenerated files with the
  authored metadata.

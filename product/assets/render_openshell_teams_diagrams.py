#!/usr/bin/env python3
"""Render the OpenShell Teams product diagrams as high-resolution PNGs."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 2000
HEIGHT = 900
SCALE = 2
OUT_DIR = Path(__file__).resolve().parent

FONT_REGULAR = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")

CHARCOAL = "#303130"
WHITE = "#FCFCFA"
TEXT = "#26364A"
MUTED = "#607084"
SLATE = "#66778C"
LIGHT_RULE = "#DCE3EA"

BLUE = "#2F6DE1"
BLUE_FILL = "#EEF4FF"
INDIGO = "#554AE8"
INDIGO_FILL = "#F1F0FF"
GREEN = "#10966E"
GREEN_FILL = "#EAF8F2"
ORANGE = "#D97706"
ORANGE_TEXT = "#A94F00"
ORANGE_FILL = "#FFF2C7"
GRAY_FILL = "#F3F6FA"


def s(value: float) -> int:
    return int(round(value * SCALE))


def pt(point: tuple[float, float]) -> tuple[int, int]:
    return s(point[0]), s(point[1])


def rect(box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return tuple(s(value) for value in box)  # type: ignore[return-value]


def font(size: float, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), s(size))


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (s(WIDTH), s(HEIGHT)), CHARCOAL)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(rect((38, 38, 1962, 862)), radius=s(30), fill=WHITE)
    return image, draw


def solid_round_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    *,
    fill: str,
    outline: str,
    width: float = 4,
    radius: float = 24,
) -> None:
    draw.rounded_rectangle(
        rect(box),
        radius=s(radius),
        fill=fill,
        outline=outline,
        width=s(width),
    )


def _dashed_line_scaled(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    fill: str,
    width: int,
    dash: int,
    gap: int,
) -> None:
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        return
    ux = dx / length
    uy = dy / length
    distance = 0.0
    while distance < length:
        segment_end = min(distance + dash, length)
        draw.line(
            (
                int(x1 + ux * distance),
                int(y1 + uy * distance),
                int(x1 + ux * segment_end),
                int(y1 + uy * segment_end),
            ),
            fill=fill,
            width=width,
        )
        distance += dash + gap


def dashed_round_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    *,
    fill: str,
    outline: str,
    width: float = 4,
    radius: float = 24,
    dash: float = 13,
    gap: float = 9,
) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(rect(box), radius=s(radius), fill=fill)

    line_width = s(width)
    dash_px = s(dash)
    gap_px = s(gap)
    _dashed_line_scaled(
        draw,
        pt((x1 + radius, y1)),
        pt((x2 - radius, y1)),
        fill=outline,
        width=line_width,
        dash=dash_px,
        gap=gap_px,
    )
    _dashed_line_scaled(
        draw,
        pt((x2, y1 + radius)),
        pt((x2, y2 - radius)),
        fill=outline,
        width=line_width,
        dash=dash_px,
        gap=gap_px,
    )
    _dashed_line_scaled(
        draw,
        pt((x2 - radius, y2)),
        pt((x1 + radius, y2)),
        fill=outline,
        width=line_width,
        dash=dash_px,
        gap=gap_px,
    )
    _dashed_line_scaled(
        draw,
        pt((x1, y2 - radius)),
        pt((x1, y1 + radius)),
        fill=outline,
        width=line_width,
        dash=dash_px,
        gap=gap_px,
    )

    corners = (
        ((x1, y1, x1 + 2 * radius, y1 + 2 * radius), 180),
        ((x2 - 2 * radius, y1, x2, y1 + 2 * radius), 270),
        ((x2 - 2 * radius, y2 - 2 * radius, x2, y2), 0),
        ((x1, y2 - 2 * radius, x1 + 2 * radius, y2), 90),
    )
    for arc_box, start_angle in corners:
        angle = start_angle
        while angle < start_angle + 90:
            draw.arc(
                rect(arc_box),
                start=angle,
                end=min(angle + 15, start_angle + 90),
                fill=outline,
                width=line_width,
            )
            angle += 25


def _text_metrics(
    draw: ImageDraw.ImageDraw,
    text: str,
    text_font: ImageFont.FreeTypeFont,
    *,
    spacing: float,
) -> tuple[int, int, tuple[int, int, int, int]]:
    bbox = draw.multiline_textbbox(
        (0, 0), text, font=text_font, spacing=s(spacing), align="center"
    )
    return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox


def centered_text(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    title: str,
    *,
    subtitle: str | None = None,
    title_size: float = 28,
    subtitle_size: float = 20,
    title_fill: str = TEXT,
    subtitle_fill: str = MUTED,
    gap: float = 10,
    title_spacing: float = 5,
    subtitle_spacing: float = 5,
) -> None:
    cx, cy = pt(center)
    title_font = font(title_size, bold=True)
    title_width, title_height, title_bbox = _text_metrics(
        draw, title, title_font, spacing=title_spacing
    )
    subtitle_font = font(subtitle_size)
    subtitle_width = subtitle_height = 0
    subtitle_bbox = (0, 0, 0, 0)
    if subtitle:
        subtitle_width, subtitle_height, subtitle_bbox = _text_metrics(
            draw, subtitle, subtitle_font, spacing=subtitle_spacing
        )

    total_height = title_height
    if subtitle:
        total_height += s(gap) + subtitle_height
    top = cy - total_height // 2

    draw.multiline_text(
        (cx - title_width // 2 - title_bbox[0], top - title_bbox[1]),
        title,
        font=title_font,
        fill=title_fill,
        spacing=s(title_spacing),
        align="center",
    )
    if subtitle:
        subtitle_top = top + title_height + s(gap)
        draw.multiline_text(
            (
                cx - subtitle_width // 2 - subtitle_bbox[0],
                subtitle_top - subtitle_bbox[1],
            ),
            subtitle,
            font=subtitle_font,
            fill=subtitle_fill,
            spacing=s(subtitle_spacing),
            align="center",
        )


def node(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    title: str,
    subtitle: str | None,
    *,
    fill: str,
    outline: str,
    dashed: bool = False,
    title_size: float = 27,
    subtitle_size: float = 19,
) -> None:
    if dashed:
        dashed_round_rect(draw, box, fill=fill, outline=outline)
    else:
        solid_round_rect(draw, box, fill=fill, outline=outline)
    x1, y1, x2, y2 = box
    centered_text(
        draw,
        ((x1 + x2) / 2, (y1 + y2) / 2),
        title,
        subtitle=subtitle,
        title_size=title_size,
        subtitle_size=subtitle_size,
        title_fill=outline if outline not in {SLATE, LIGHT_RULE} else TEXT,
    )


def container_label(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    title: str,
    subtitle: str,
    *,
    fill: str = TEXT,
) -> None:
    x, y = pt(position)
    draw.text((x, y), title, font=font(27, bold=True), fill=fill)
    draw.text((x, y + s(36)), subtitle, font=font(19), fill=MUTED)


def pill(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    text: str,
    *,
    text_fill: str = MUTED,
    fill: str = WHITE,
    outline: str | None = None,
    size: float = 20,
    padding_x: float = 14,
    padding_y: float = 8,
) -> None:
    text_font = font(size, bold=False)
    width, height, bbox = _text_metrics(draw, text, text_font, spacing=4)
    cx, cy = pt(center)
    x1 = cx - width // 2 - s(padding_x)
    y1 = cy - height // 2 - s(padding_y)
    x2 = cx + width // 2 + s(padding_x)
    y2 = cy + height // 2 + s(padding_y)
    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=s(10),
        fill=fill,
        outline=outline,
        width=s(2) if outline else 1,
    )
    draw.multiline_text(
        (cx - width // 2 - bbox[0], cy - height // 2 - bbox[1]),
        text,
        font=text_font,
        fill=text_fill,
        spacing=s(4),
        align="center",
    )


def _arrow_head(
    draw: ImageDraw.ImageDraw,
    tip: tuple[int, int],
    previous: tuple[int, int],
    *,
    fill: str,
    size: float = 15,
) -> None:
    dx = tip[0] - previous[0]
    dy = tip[1] - previous[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return
    ux = dx / length
    uy = dy / length
    px = -uy
    py = ux
    head = s(size)
    half = head * 0.55
    base_x = tip[0] - ux * head
    base_y = tip[1] - uy * head
    draw.polygon(
        (
            tip,
            (int(base_x + px * half), int(base_y + py * half)),
            (int(base_x - px * half), int(base_y - py * half)),
        ),
        fill=fill,
    )


def arrow(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    *,
    color: str = SLATE,
    width: float = 4,
    dashed: bool = False,
    start_head: bool = False,
    end_head: bool = True,
) -> None:
    scaled = [pt(point) for point in points]
    if dashed:
        for start, end in zip(scaled, scaled[1:]):
            _dashed_line_scaled(
                draw,
                start,
                end,
                fill=color,
                width=s(width),
                dash=s(12),
                gap=s(9),
            )
    else:
        draw.line(scaled, fill=color, width=s(width), joint="curve")
    if end_head:
        _arrow_head(draw, scaled[-1], scaled[-2], fill=color)
    if start_head:
        _arrow_head(draw, scaled[0], scaled[1], fill=color)


def chip(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    text: str,
    *,
    fill: str,
    outline: str,
) -> None:
    solid_round_rect(draw, box, fill=fill, outline=outline, width=3, radius=16)
    x1, y1, x2, y2 = box
    centered_text(
        draw,
        ((x1 + x2) / 2, (y1 + y2) / 2),
        text,
        title_size=20,
        title_fill=outline,
    )


def save(image: Image.Image, name: str) -> None:
    result = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    result.save(OUT_DIR / name, format="PNG", optimize=True)


def render_workspace_boundaries() -> None:
    image, draw = canvas()

    solid_round_rect(
        draw,
        (90, 285, 1910, 815),
        fill="#FAFBFD",
        outline=SLATE,
        width=4,
        radius=28,
    )
    solid_round_rect(
        draw,
        (220, 390, 1500, 700),
        fill=BLUE_FILL,
        outline=BLUE,
        width=4,
        radius=26,
    )
    container_label(
        draw,
        (135, 320),
        "OpenShell workspace",
        "Human access + resource boundary",
    )
    container_label(
        draw,
        (260, 415),
        "Team",
        "Collaboration boundary for selected sandboxes",
        fill=BLUE,
    )

    # Application-to-sandbox correlation sits behind the sandbox cards.
    arrow(
        draw,
        [(1000, 240), (1000, 475)],
        color=ORANGE,
        dashed=True,
        end_head=False,
    )
    arrow(
        draw,
        [(425, 475), (1255, 475)],
        color=ORANGE,
        dashed=True,
        end_head=False,
    )
    arrow(
        draw,
        [(425, 475), (425, 505)],
        color=ORANGE,
        dashed=True,
        end_head=False,
    )
    arrow(
        draw,
        [(825, 475), (825, 505)],
        color=ORANGE,
        dashed=True,
        end_head=False,
    )
    arrow(
        draw,
        [(1255, 475), (1255, 505)],
        color=ORANGE,
        dashed=True,
        end_head=False,
    )
    pill(
        draw,
        (1000, 318),
        "Sandbox ID correlation only",
        text_fill=ORANGE_TEXT,
        fill=WHITE,
        size=19,
    )

    # Team and authority relationships.
    arrow(draw, [(550, 565), (700, 565)], start_head=True)
    pill(draw, (625, 660), "Team collaboration", size=18, fill=BLUE_FILL)
    arrow(draw, [(950, 565), (1120, 565)])
    pill(
        draw,
        (1035, 660),
        "Creates child\nNarrower authority",
        size=18,
        fill=BLUE_FILL,
    )
    arrow(draw, [(650, 755), (825, 755), (825, 625)], color=GREEN)
    pill(
        draw,
        (775, 722),
        "Explicit attachment",
        text_fill=GREEN,
        size=18,
        fill="#FAFBFD",
    )

    node(
        draw,
        (300, 505, 550, 625),
        "Sandbox A",
        "Team member\nID: A",
        fill=BLUE_FILL,
        outline=BLUE,
    )
    node(
        draw,
        (700, 505, 950, 625),
        "Sandbox B",
        "Peer sandbox\nID: B",
        fill=INDIGO_FILL,
        outline=INDIGO,
    )
    node(
        draw,
        (1120, 505, 1390, 625),
        "Sandbox D",
        "Child of B\nID: D",
        fill=GREEN_FILL,
        outline=GREEN,
    )
    node(
        draw,
        (1580, 475, 1840, 625),
        "Sandbox S",
        "Same workspace\nNot on this team",
        fill=GRAY_FILL,
        outline=SLATE,
    )
    node(
        draw,
        (260, 720, 650, 790),
        "Workspace-approved resource",
        "Attached only to Sandbox B",
        fill=GREEN_FILL,
        outline=GREEN,
        title_size=22,
        subtitle_size=17,
    )
    node(
        draw,
        (560, 78, 1440, 240),
        "Optional application session / run",
        "Application-owned task model • not an OpenShell resource\nThe owning application may run inside a sandbox",
        fill=ORANGE_FILL,
        outline=ORANGE,
        dashed=True,
        title_size=29,
        subtitle_size=20,
    )

    save(image, "openshell-teams-workspace-boundaries-v2.png")


def render_authority_paths() -> None:
    image, draw = canvas()

    draw.line((s(830), s(105), s(830), s(800)), fill=LIGHT_RULE, width=s(3))
    draw.text(
        pt((450, 100)),
        "Delegated child",
        font=font(31, bold=True),
        fill=TEXT,
        anchor="mm",
    )
    draw.text(
        pt((1370, 100)),
        "Peer sandbox",
        font=font(31, bold=True),
        fill=TEXT,
        anchor="mm",
    )

    # Left: a parent can only delegate a subset.
    arrow(draw, [(450, 360), (450, 430)])
    arrow(draw, [(450, 540), (450, 610)])
    node(
        draw,
        (190, 220, 710, 360),
        "Parent sandbox",
        "Delegable authority",
        fill=BLUE_FILL,
        outline=BLUE,
        title_size=30,
        subtitle_size=22,
    )
    node(
        draw,
        (260, 430, 640, 540),
        "OpenShell limits delegation",
        "Child cannot exceed\nparent authority",
        fill=GREEN_FILL,
        outline=GREEN,
        title_size=25,
        subtitle_size=19,
    )
    node(
        draw,
        (190, 610, 710, 760),
        "Child sandbox",
        "Narrower policy, resources,\nor lifetime",
        fill=INDIGO_FILL,
        outline=INDIGO,
        title_size=30,
        subtitle_size=21,
    )

    # Right: a peer can receive separate, administrator-approved authority.
    arrow(draw, [(1250, 260), (1400, 260)])
    arrow(draw, [(1635, 350), (1635, 390), (1580, 390), (1580, 450)])
    pill(draw, (1725, 397), "Defines B's authority", size=18, fill=WHITE)
    arrow(
        draw,
        [(1240, 525), (1430, 525)],
        color=ORANGE,
        dashed=True,
    )
    pill(
        draw,
        (1335, 455),
        "Request only\nNo authority transfer",
        text_fill=ORANGE_TEXT,
        size=18,
        fill=WHITE,
    )
    arrow(draw, [(1580, 600), (1580, 690)])
    pill(
        draw,
        (1750, 645),
        "Delegable subset only",
        size=18,
        fill=WHITE,
    )

    node(
        draw,
        (930, 200, 1250, 320),
        "Human workspace\nadministrator",
        None,
        fill=BLUE_FILL,
        outline=BLUE,
        title_size=25,
    )
    node(
        draw,
        (1400, 170, 1870, 350),
        "Sandbox policy + launch settings",
        "Approved by the workspace administrator\nfor this creation only\nRepository + approved network",
        fill=ORANGE_FILL,
        outline=ORANGE,
        dashed=True,
        title_size=25,
        subtitle_size=19,
    )
    node(
        draw,
        (930, 450, 1240, 600),
        "Requesting sandbox",
        "No external network access",
        fill=BLUE_FILL,
        outline=BLUE,
        title_size=26,
        subtitle_size=21,
    )
    node(
        draw,
        (1430, 450, 1730, 600),
        "Peer sandbox B",
        "Runs with approved settings",
        fill=GREEN_FILL,
        outline=GREEN,
        title_size=26,
        subtitle_size=20,
    )
    node(
        draw,
        (1430, 690, 1730, 810),
        "Child sandbox D",
        "Narrower repository + network\nNo inference",
        fill=GRAY_FILL,
        outline=SLATE,
        title_size=25,
        subtitle_size=18,
    )

    save(image, "openshell-teams-authority-paths-v2.png")


def render_observability_views() -> None:
    image, draw = canvas()

    draw.text(
        pt((1000, 92)),
        "One set of sandboxes. Two ownership views.",
        font=font(32, bold=True),
        fill=TEXT,
        anchor="mm",
    )
    draw.text(
        pt((1000, 140)),
        "The meta-harness can run inside a sandbox; these views describe ownership, not location.",
        font=font(20),
        fill=MUTED,
        anchor="mm",
    )

    solid_round_rect(
        draw,
        (90, 200, 850, 745),
        fill="#F5F5FF",
        outline=INDIGO,
        radius=28,
    )
    solid_round_rect(
        draw,
        (1150, 200, 1910, 745),
        fill="#EFF9F4",
        outline=GREEN,
        radius=28,
    )
    container_label(
        draw,
        (130, 235),
        "Application / meta-harness view",
        "Owns work meaning",
        fill=INDIGO,
    )
    container_label(
        draw,
        (1190, 235),
        "OpenShell runtime view",
        "Owns runtime truth",
        fill=GREEN,
    )

    arrow(draw, [(470, 405), (470, 460)])
    node(
        draw,
        (190, 300, 750, 405),
        "Application interface",
        "Web • CLI • API",
        fill=BLUE_FILL,
        outline=BLUE,
        title_size=28,
        subtitle_size=21,
    )
    solid_round_rect(
        draw,
        (155, 460, 785, 700),
        fill=INDIGO_FILL,
        outline=INDIGO,
    )
    centered_text(
        draw,
        (470, 540),
        "Optional application work model",
        subtitle="Sessions / runs • Tasks • Progress\nRetries • Results • Application telemetry",
        title_size=28,
        subtitle_size=21,
        title_fill=INDIGO,
    )
    chip(draw, (225, 625, 365, 675), "Sandbox A", fill=BLUE_FILL, outline=BLUE)
    chip(draw, (400, 625, 540, 675), "Sandbox B", fill=INDIGO_FILL, outline=INDIGO)
    chip(draw, (575, 625, 715, 675), "Sandbox C", fill=GREEN_FILL, outline=GREEN)

    solid_round_rect(
        draw,
        (1200, 300, 1860, 690),
        fill=WHITE,
        outline=SLATE,
        width=3,
        radius=22,
    )
    draw.text(pt((1230, 325)), "Workspace", font=font(23, bold=True), fill=TEXT)
    solid_round_rect(
        draw,
        (1240, 365, 1820, 495),
        fill=GREEN_FILL,
        outline=GREEN,
        width=3,
        radius=20,
    )
    draw.text(pt((1270, 388)), "Team", font=font(22, bold=True), fill=GREEN)
    chip(draw, (1280, 425, 1430, 470), "Sandbox A", fill=BLUE_FILL, outline=BLUE)
    chip(draw, (1455, 425, 1605, 470), "Sandbox B", fill=INDIGO_FILL, outline=INDIGO)
    chip(draw, (1630, 425, 1780, 470), "Sandbox C", fill=GREEN_FILL, outline=GREEN)

    node(
        draw,
        (1235, 530, 1425, 600),
        "Lineage +\nlifecycle",
        None,
        fill=GRAY_FILL,
        outline=SLATE,
        title_size=18,
    )
    node(
        draw,
        (1435, 530, 1625, 600),
        "Policy +\ndenials",
        None,
        fill=GRAY_FILL,
        outline=SLATE,
        title_size=18,
    )
    node(
        draw,
        (1635, 530, 1825, 600),
        "Forum +\npeer events",
        None,
        fill=GRAY_FILL,
        outline=SLATE,
        title_size=18,
    )
    node(
        draw,
        (1235, 620, 1825, 675),
        "Raw runtime + sandbox application logs",
        None,
        fill="#EEF2F6",
        outline=SLATE,
        title_size=20,
    )

    arrow(
        draw,
        [(850, 455), (1150, 455)],
        start_head=False,
        end_head=False,
    )
    pill(draw, (1000, 420), "Same sandbox IDs", size=19, fill=WHITE)
    arrow(
        draw,
        [(850, 545), (1150, 545)],
        color=ORANGE,
        dashed=True,
        start_head=False,
        end_head=False,
    )
    pill(
        draw,
        (1000, 585),
        "Correlation labels (optional)",
        text_fill=ORANGE_TEXT,
        size=18,
        fill=WHITE,
    )

    draw.text(
        pt((1000, 808)),
        "The application explains the work. OpenShell explains the runtime.",
        font=font(23, bold=True),
        fill=TEXT,
        anchor="mm",
    )

    save(image, "openshell-teams-observability-views.png")


def main() -> None:
    render_workspace_boundaries()
    render_authority_paths()
    render_observability_views()


if __name__ == "__main__":
    main()

"""Render the Privacy Guard latency proof-of-concept figure as deterministic SVG."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

_ANALYSIS_DIR = Path(__file__).resolve().parent
_DEFAULT_DATA = _ANALYSIS_DIR / "privacy-guard-latency.csv"
_DEFAULT_OUTPUT = (
    _ANALYSIS_DIR.parent
    / "docs"
    / "assets"
    / "analysis"
    / "privacy-guard-latency-vs-prompt-size.svg"
)

_WIDTH = 1200
_HEIGHT = 720
_MARGIN_LEFT = 105
_MARGIN_RIGHT = 65
_MARGIN_TOP = 75
_MARGIN_BOTTOM = 105
_X_MIN = 10_000.0
_X_MAX = 1_200_000.0
_Y_MIN = 4.0
_Y_MAX = 400.0
_CONTEXT_THRESHOLD = 1_000_000.0
_COLORBAR_X = 650
_COLORBAR_WIDTH = 330
_ENTITY_COLORS = (
    (0.00, (43, 10, 61)),
    (0.25, (123, 47, 142)),
    (0.50, (213, 82, 105)),
    (0.75, (245, 137, 76)),
    (1.00, (247, 209, 61)),
)


@dataclass(frozen=True)
class Measurement:
    """One joined latency observation."""

    observed_at_utc: str
    prompt_tokens: int
    privacy_guard_latency_ms: float
    entity_count: int
    phase: str
    openshell_observed_ms: float | None
    first_output_elapsed_ms: float | None
    turn_elapsed_ms: float | None


@dataclass(frozen=True)
class LinearFit:
    """Ordinary least-squares fit of latency against tokens."""

    intercept_ms: float
    milliseconds_per_100k_tokens: float
    r_squared: float

    def predict(self, prompt_tokens: float) -> float:
        """Return the fitted latency for one prompt size."""
        return self.intercept_ms + self.milliseconds_per_100k_tokens * (
            prompt_tokens / 100_000.0
        )


def main() -> None:
    """Load observations and write or verify the generated SVG."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=_DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when the committed SVG does not match a fresh render.",
    )
    args = parser.parse_args()

    measurements = _load_measurements(args.data)
    fit = _linear_fit(measurements)
    svg = _render_svg(measurements, fit)

    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != svg:
            raise SystemExit(
                f"{args.output} is missing or stale; rerun without --check"
            )
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8")

    completed_turns = [row for row in measurements if row.turn_elapsed_ms is not None]
    mean_turn_share = statistics.fmean(
        row.privacy_guard_latency_ms / row.turn_elapsed_ms
        for row in completed_turns
        if row.turn_elapsed_ms is not None
    )
    print(
        f"{len(measurements)} measurements; "
        f"fit={fit.intercept_ms:.2f} + "
        f"{fit.milliseconds_per_100k_tokens:.2f} ms/100k tokens; "
        f"R²={fit.r_squared:.3f}; "
        f"mean turn share={100.0 * mean_turn_share:.2f}%"
    )


def _load_measurements(path: Path) -> list[Measurement]:
    rows: list[Measurement] = []
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            rows.append(
                Measurement(
                    observed_at_utc=row["observed_at_utc"],
                    prompt_tokens=int(row["prompt_tokens"]),
                    privacy_guard_latency_ms=float(row["privacy_guard_latency_ms"]),
                    entity_count=int(row["entity_count"]),
                    phase=row["phase"],
                    openshell_observed_ms=_optional_float(row["openshell_observed_ms"]),
                    first_output_elapsed_ms=_optional_float(
                        row["first_output_elapsed_ms"]
                    ),
                    turn_elapsed_ms=_optional_float(row["turn_elapsed_ms"]),
                )
            )
    if len(rows) < 2:
        raise ValueError("at least two measurements are required")
    return rows


def _optional_float(value: str) -> float | None:
    return float(value) if value else None


def _linear_fit(measurements: list[Measurement]) -> LinearFit:
    x_values = [row.prompt_tokens / 100_000.0 for row in measurements]
    y_values = [row.privacy_guard_latency_ms for row in measurements]
    x_mean = statistics.fmean(x_values)
    y_mean = statistics.fmean(y_values)
    x_variance = sum((value - x_mean) ** 2 for value in x_values)
    if x_variance == 0.0:
        raise ValueError("prompt-token observations must not all be equal")
    slope = (
        sum(
            (x_value - x_mean) * (y_value - y_mean)
            for x_value, y_value in zip(x_values, y_values, strict=True)
        )
        / x_variance
    )
    intercept = y_mean - slope * x_mean
    residual_sum = sum(
        (y_value - (intercept + slope * x_value)) ** 2
        for x_value, y_value in zip(x_values, y_values, strict=True)
    )
    total_sum = sum((value - y_mean) ** 2 for value in y_values)
    r_squared = 1.0 - residual_sum / total_sum
    return LinearFit(
        intercept_ms=intercept,
        milliseconds_per_100k_tokens=slope,
        r_squared=r_squared,
    )


def _render_svg(measurements: list[Measurement], fit: LinearFit) -> str:
    plot_right = _WIDTH - _MARGIN_RIGHT
    plot_bottom = _HEIGHT - _MARGIN_BOTTOM
    minimum_entities = min(row.entity_count for row in measurements)
    maximum_entities = max(row.entity_count for row in measurements)
    completed_turns = [row for row in measurements if row.turn_elapsed_ms is not None]
    mean_turn_share = statistics.fmean(
        row.privacy_guard_latency_ms / row.turn_elapsed_ms
        for row in completed_turns
        if row.turn_elapsed_ms is not None
    )

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {_WIDTH} {_HEIGHT}" role="img" '
            f'aria-labelledby="title description">'
        ),
        '<title id="title">Privacy Guard latency versus prompt size</title>',
        (
            '<desc id="description">Scatter plot of 96 Privacy Guard service '
            "latency measurements from 18 thousand to 1.141 million prompt "
            "tokens, with one linear fit and a one-million-token threshold. "
            f"Privacy Guard averaged {100.0 * mean_turn_share:.2f} percent of "
            "end-to-end time across 12 completed turns.</desc>"
        ),
        "<defs>",
        '<linearGradient id="entity-scale" x1="0%" y1="0%" x2="100%" y2="0%">',
        *[
            f'<stop offset="{position:.0%}" stop-color="{_rgb_hex(color)}"/>'
            for position, color in _ENTITY_COLORS
        ],
        "</linearGradient>",
        "<style>",
        (
            'text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,'
            "Helvetica,Arial,sans-serif;fill:#1f2933}"
        ),
        ".axis{font-size:18px;fill:#5f6b76}",
        ".label{font-size:19px;font-weight:600}",
        ".annotation{font-size:18px;fill:#66717c}",
        ".grid{stroke:#dfe4e8;stroke-width:1}",
        ".fit{fill:none;stroke:#ed6a24;stroke-width:3}",
        ".point{fill-opacity:.86;stroke:#ffffff;stroke-width:1.5}",
        (".threshold{stroke:#20262d;stroke-width:2;stroke-dasharray:8 7}"),
        (
            "@media(prefers-color-scheme:dark){"
            "text{fill:#edf2f7}"
            ".axis,.annotation{fill:#aeb8c5}"
            ".grid{stroke:#52606d}"
            ".threshold{stroke:#d8dee5}"
            ".point{stroke:#e5e9ee}"
            "}"
        ),
        "</style></defs>",
        (
            f'<text x="{_MARGIN_LEFT}" y="38" class="label">'
            "Privacy Guard latency (ms) · log scale</text>"
        ),
    ]

    for value in (10_000, 30_000, 100_000, 300_000, 1_000_000):
        x = _x(value)
        parts.append(
            f'<line x1="{x:.2f}" x2="{x:.2f}" y1="{_MARGIN_TOP}" '
            f'y2="{plot_bottom}" class="grid"/>'
        )
        anchor = "start" if value == 10_000 else "middle"
        parts.append(
            f'<text x="{x:.2f}" y="{plot_bottom + 32}" '
            f'text-anchor="{anchor}" class="axis">{_format_tokens(value)}</text>'
        )

    for value in (5, 10, 20, 50, 100, 250):
        y = _y(value)
        parts.append(
            f'<line x1="{_MARGIN_LEFT}" x2="{plot_right}" y1="{y:.2f}" '
            f'y2="{y:.2f}" class="grid"/>'
        )
        parts.append(
            f'<text x="{_MARGIN_LEFT - 16}" y="{y + 6:.2f}" '
            f'text-anchor="end" class="axis">{value}</text>'
        )

    threshold_x = _x(_CONTEXT_THRESHOLD)
    parts.extend(
        [
            (
                f'<line x1="{threshold_x:.2f}" x2="{threshold_x:.2f}" '
                f'y1="{_MARGIN_TOP}" y2="{plot_bottom}" class="threshold"/>'
            ),
            (
                f'<text x="{threshold_x - 12:.2f}" y="{_y(84):.2f}" '
                'text-anchor="end" class="annotation">1M threshold</text>'
            ),
        ]
    )

    fit_points = []
    token_value = _X_MIN
    while token_value < _X_MAX:
        fit_points.append(f"{_x(token_value):.2f},{_y(fit.predict(token_value)):.2f}")
        token_value *= 1.06
    fit_points.append(f"{_x(_X_MAX):.2f},{_y(fit.predict(_X_MAX)):.2f}")
    parts.append(f'<polyline points="{" ".join(fit_points)}" class="fit"/>')

    for row in measurements:
        point_color = _entity_color(
            row.entity_count,
            minimum=minimum_entities,
            maximum=maximum_entities,
        )
        parts.append(
            f'<circle cx="{_x(row.prompt_tokens):.2f}" '
            f'cy="{_y(row.privacy_guard_latency_ms):.2f}" '
            f'r="6" class="point" fill="{point_color}">'
            f"<title>{row.prompt_tokens:,} tokens; "
            f"{row.privacy_guard_latency_ms:.1f} ms; "
            f"{row.entity_count} entities detected</title></circle>"
        )

    share_x = _x(14_000)
    share_y = _y(90)
    parts.extend(
        [
            (
                f'<text x="{share_x:.2f}" y="{share_y:.2f}" class="label">'
                f"Privacy Guard averaged {100.0 * mean_turn_share:.2f}%</text>"
            ),
            (
                f'<text x="{share_x:.2f}" y="{share_y + 28:.2f}" '
                'class="annotation">of end-to-end turn time</text>'
            ),
            (
                f'<text x="{share_x:.2f}" y="{share_y + 55:.2f}" '
                f'class="annotation">across {len(completed_turns)} '
                "completed turns</text>"
            ),
        ]
    )

    parts.extend(
        [
            (
                f'<text x="{_COLORBAR_X}" y="{plot_bottom - 55}" '
                'class="annotation">'
                "Entities detected</text>"
            ),
            (
                f'<rect x="{_COLORBAR_X}" y="{plot_bottom - 43}" '
                f'width="{_COLORBAR_WIDTH}" height="14" '
                'rx="2" fill="url(#entity-scale)"/>'
            ),
            (
                f'<text x="{plot_right}" y="{_HEIGHT - 48}" text-anchor="end" '
                'class="axis">Prompt tokens · log scale</text>'
            ),
            (
                f'<text x="{_MARGIN_LEFT}" y="{_HEIGHT - 16}" class="annotation">'
                f"Single fit across {len(measurements)} measurements: "
                f"{fit.intercept_ms:.2f} + "
                f"{fit.milliseconds_per_100k_tokens:.2f} ms per 100k tokens · "
                f"R² {fit.r_squared:.3f}</text>"
            ),
        ]
    )
    for tick in (minimum_entities, 100, 200, 300, maximum_entities):
        tick_x = _COLORBAR_X + _COLORBAR_WIDTH * (
            (tick - minimum_entities) / (maximum_entities - minimum_entities)
        )
        parts.append(
            f'<line x1="{tick_x:.2f}" x2="{tick_x:.2f}" '
            f'y1="{plot_bottom - 29}" y2="{plot_bottom - 24}" '
            'stroke="#66717c" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{tick_x:.2f}" y="{plot_bottom - 8}" '
            f'text-anchor="middle" class="axis">{tick}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _x(value: float) -> float:
    fraction = (math.log10(value) - math.log10(_X_MIN)) / (
        math.log10(_X_MAX) - math.log10(_X_MIN)
    )
    return _MARGIN_LEFT + fraction * (_WIDTH - _MARGIN_LEFT - _MARGIN_RIGHT)


def _y(value: float) -> float:
    fraction = (math.log10(value) - math.log10(_Y_MIN)) / (
        math.log10(_Y_MAX) - math.log10(_Y_MIN)
    )
    return (
        _HEIGHT - _MARGIN_BOTTOM - fraction * (_HEIGHT - _MARGIN_TOP - _MARGIN_BOTTOM)
    )


def _format_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.0f}M"
    return f"{value // 1_000}k"


def _entity_color(value: int, *, minimum: int, maximum: int) -> str:
    if maximum == minimum:
        return _rgb_hex(_ENTITY_COLORS[len(_ENTITY_COLORS) // 2][1])
    fraction = (value - minimum) / (maximum - minimum)
    for (left_position, left_color), (
        right_position,
        right_color,
    ) in zip(_ENTITY_COLORS, _ENTITY_COLORS[1:], strict=True):
        if fraction <= right_position:
            segment = (fraction - left_position) / (right_position - left_position)
            color = (
                round(left_color[0] + segment * (right_color[0] - left_color[0])),
                round(left_color[1] + segment * (right_color[1] - left_color[1])),
                round(left_color[2] + segment * (right_color[2] - left_color[2])),
            )
            return _rgb_hex(color)
    return _rgb_hex(_ENTITY_COLORS[-1][1])


def _rgb_hex(color: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in color)


if __name__ == "__main__":
    main()

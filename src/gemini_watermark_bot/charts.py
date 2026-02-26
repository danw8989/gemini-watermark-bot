"""Generate PNG chart images for the /stats command."""

from __future__ import annotations

import datetime
import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402


# Shared dark theme colours
_BG = "#1e1e2e"
_FG = "#cdd6f4"
_ACCENT = "#89b4fa"
_ACCENT2 = "#a6e3a1"
_GRID = "#45475a"


def generate_overview_chart(
    total: int,
    unique: int,
    today_imgs: int,
    today_users: int,
    daily_data: dict[str, dict],
) -> io.BytesIO:
    """Return a PNG buffer with a 7-day bar chart and summary stats."""
    # Prepare last 7 days of data
    dates = []
    counts = []
    for i in range(6, -1, -1):
        d = datetime.date.today() - datetime.timedelta(days=i)
        ds = str(d)
        day = daily_data.get(ds, {"images": 0, "users": set()})
        dates.append(d.strftime("%b %d"))
        counts.append(day["images"])

    fig, (ax_bar, ax_text) = plt.subplots(
        2, 1,
        figsize=(8, 5),
        gridspec_kw={"height_ratios": [3, 1]},
        facecolor=_BG,
    )

    # --- Bar chart ---
    ax_bar.set_facecolor(_BG)
    bars = ax_bar.bar(dates, counts, color=_ACCENT, edgecolor=_ACCENT, width=0.6)
    ax_bar.set_ylabel("Images", color=_FG, fontsize=11)
    ax_bar.set_title("Last 7 Days", color=_FG, fontsize=14, fontweight="bold")
    ax_bar.tick_params(colors=_FG, labelsize=10)
    ax_bar.spines["bottom"].set_color(_GRID)
    ax_bar.spines["left"].set_color(_GRID)
    ax_bar.spines["top"].set_visible(False)
    ax_bar.spines["right"].set_visible(False)
    ax_bar.yaxis.grid(True, color=_GRID, linestyle="--", alpha=0.5)
    ax_bar.set_axisbelow(True)

    # Value labels on bars
    for bar, count in zip(bars, counts):
        if count > 0:
            ax_bar.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(counts) * 0.02,
                str(count),
                ha="center", va="bottom",
                color=_FG, fontsize=10, fontweight="bold",
            )

    # --- Summary text ---
    ax_text.set_facecolor(_BG)
    ax_text.axis("off")
    summary = (
        f"Total images: {total:,}    "
        f"Unique users: {unique:,}    "
        f"Today: {today_imgs} images, {today_users} users"
    )
    ax_text.text(
        0.5, 0.5, summary,
        ha="center", va="center",
        color=_FG, fontsize=12,
        transform=ax_text.transAxes,
        bbox=dict(boxstyle="round,pad=0.5", facecolor=_GRID, alpha=0.6),
    )

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_top_users_chart(
    user_counts: dict[int, int],
    limit: int = 10,
) -> io.BytesIO | None:
    """Return a PNG buffer with a horizontal bar chart of top users, or None."""
    if not user_counts:
        return None

    top = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    labels = [f"#{uid}" for uid, _ in top]
    values = [cnt for _, cnt in top]

    # Reverse so the top user is at the top of the chart
    labels.reverse()
    values.reverse()

    fig_height = max(3, 0.5 * len(labels) + 1)
    fig, ax = plt.subplots(figsize=(8, fig_height), facecolor=_BG)
    ax.set_facecolor(_BG)

    bars = ax.barh(labels, values, color=_ACCENT2, edgecolor=_ACCENT2, height=0.6)
    ax.set_xlabel("Images processed", color=_FG, fontsize=11)
    ax.set_title("Top Users (all-time)", color=_FG, fontsize=14, fontweight="bold")
    ax.tick_params(colors=_FG, labelsize=10)
    ax.spines["bottom"].set_color(_GRID)
    ax.spines["left"].set_color(_GRID)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.xaxis.grid(True, color=_GRID, linestyle="--", alpha=0.5)
    ax.set_axisbelow(True)

    # Value labels on bars
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_width() + max(values) * 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"{val:,}",
            ha="left", va="center",
            color=_FG, fontsize=10, fontweight="bold",
        )

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf

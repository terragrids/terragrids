"""The worked example of the population weighting, drawn: one area of four cells.

The area covers four weather cells: hills, a coast, suburbs and a city, each with its
own temperature and its own number of people. The library computes the area's values,
and the drawing shows where each one comes from:

- the plain average of the four cells, against the average over the area's people;
- the area's people lined up from the coolest cell to the hottest, with the 10th, 50th
  and 90th percentiles, and the share of people above 32 °C.

It needs no data. Run it from the repository's root, with the study's extra tools
installed::

    pip install -e ".[study]"
    python docs/methodology/aggregation_example.py

It writes ``figures/aggregation_worked_example.svg``, next to this file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from terragrids.areas import AreaWeights

FIGURE = Path(__file__).resolve().parent / "figures" / "aggregation_worked_example.svg"


@dataclass(frozen=True)
class Cell:
    """One weather cell of the example: what it is, its temperature and its people."""

    name: str
    value: float
    people: int
    row: int
    column: int


class WorkedExample:
    """The four-cell area, its values from the library, and the drawing of them."""

    CELLS = (
        Cell("hills", 25.0, 50_000, 0, 0),
        Cell("coast", 28.0, 150_000, 0, 1),
        Cell("suburbs", 34.0, 300_000, 1, 0),
        Cell("city", 37.0, 500_000, 1, 1),
    )
    THRESHOLD = 32.0
    # The grid's cell centres: two latitudes and two longitudes, 0.25° apart.
    LAT = (45.25, 45.0)
    LON = (7.0, 7.25)
    HEAT = "RdYlBu_r"
    LIMITS = (20.0, 40.0)

    def __init__(self) -> None:
        self.values = np.full((2, 2), np.nan)
        for cell in self.CELLS:
            self.values[cell.row, cell.column] = cell.value
        # One population point at each cell's centre, and one area around all four.
        point_lat = [self.LAT[cell.row] for cell in self.CELLS]
        point_lon = [self.LON[cell.column] for cell in self.CELLS]
        people = [cell.people for cell in self.CELLS]
        area = {"example": shapely.box(6.875, 44.875, 7.375, 45.375)}
        self.weights = AreaWeights.from_population(
            area, point_lat, point_lon, people, self.LAT, self.LON
        )

    def numbers(self) -> dict[str, float]:
        """The area's values, as the library computes them."""
        p10, p50, p90 = self.weights.percentiles(self.values)[0]
        return {
            "plain": float(np.mean([cell.value for cell in self.CELLS])),
            "mean": float(self.weights.apply(self.values).mean[0]),
            "p10": float(p10),
            "p50": float(p50),
            "p90": float(p90),
            "above": float(self.weights.share_above(self.values, self.THRESHOLD)[0]),
        }

    def draw(self, path: Path) -> None:
        """Draws the example: the cells, the two averages, and the people lined up."""
        import matplotlib

        matplotlib.use("svg")
        matplotlib.rcParams.update({"svg.hashsalt": "terragrids", "svg.fonttype": "none"})
        matplotlib.rcParams.update({"font.size": 8})
        import matplotlib.pyplot as plt

        numbers = self.numbers()
        figure = plt.figure(figsize=(7.6, 4.6))
        grid = figure.add_gridspec(2, 2, height_ratios=[1.35, 1], width_ratios=[1, 1.25])
        self.draw_cells(figure.add_subplot(grid[0, 0]))
        self.draw_averages(figure.add_subplot(grid[0, 1]), numbers)
        self.draw_line_up(figure.add_subplot(grid[1, :]), numbers)
        figure.subplots_adjust(left=0.04, right=0.95, top=0.92, bottom=0.1, hspace=0.55)
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(path, format="svg", metadata={"Date": None, "Creator": None})
        plt.close(figure)
        compact = re.sub(
            r'd="([^"]*)"', lambda m: f'd="{self.compact(m.group(1))}"', path.read_text()
        )
        path.write_text(compact)

    def draw_cells(self, axis: Any) -> None:
        """The area's four cells, each with its temperature and its people."""
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle

        palette = plt.get_cmap(self.HEAT)
        scale = plt.Normalize(*self.LIMITS)
        for cell in self.CELLS:
            x, y = cell.column, 1 - cell.row
            colour = palette(scale(cell.value))
            axis.add_patch(Rectangle((x, y), 1, 1, fc=colour, ec="white", lw=2))
            axis.text(x + 0.5, y + 0.66, cell.name, ha="center", fontsize=9, weight="bold")
            axis.text(x + 0.5, y + 0.43, f"{cell.value:.0f} °C", ha="center", fontsize=9)
            axis.text(x + 0.5, y + 0.2, f"{cell.people:,} people", ha="center", fontsize=6.5)
        axis.add_patch(Rectangle((0, 0), 2, 2, fill=False, ec="#1d3557", lw=1.5))
        axis.set_xlim(-0.05, 2.05)
        axis.set_ylim(-0.05, 2.05)
        axis.set_aspect("equal")
        axis.set_axis_off()
        axis.set_title("one area, four weather cells", fontsize=9)

    def draw_averages(self, axis: Any, numbers: dict[str, float]) -> None:
        """The plain average of the cells against the average over the people."""
        bars = [
            ("plain average of the 4 cells: each cell counts once", numbers["plain"], "#9aa5b1"),
            ("average over the people: each cell counts by its people", numbers["mean"], "#e76f51"),
        ]
        for position, (label, value, colour) in enumerate(bars):
            axis.barh(position, value, color=colour, height=0.45)
            axis.text(0, position - 0.3, label, va="bottom", fontsize=8)
            axis.text(
                value + 0.4, position, f"{value:.2f} °C", va="center", fontsize=9, weight="bold"
            )
        axis.set_yticks([])
        axis.set_ylim(1.4, -0.9)
        axis.set_xlim(0, 45)
        axis.set_xlabel("°C")
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_title("two ways to give the area one temperature", fontsize=9)

    def draw_line_up(self, axis: Any, numbers: dict[str, float]) -> None:
        """The area's people from the coolest cell to the hottest, with the percentiles."""
        import matplotlib.pyplot as plt

        palette = plt.get_cmap(self.HEAT)
        scale = plt.Normalize(*self.LIMITS)
        start = 0
        for cell in sorted(self.CELLS, key=lambda c: c.value):
            axis.barh(
                0,
                cell.people,
                left=start,
                height=0.5,
                color=palette(scale(cell.value)),
                edgecolor="white",
                lw=1.5,
            )
            axis.text(
                start + cell.people / 2,
                0,
                f"{cell.name}\n{cell.value:.0f} °C",
                ha="center",
                va="center",
                fontsize=7.5,
            )
            start += cell.people
        total = start
        for share, key in [(0.1, "p10"), (0.5, "p50"), (0.9, "p90")]:
            axis.plot([share * total] * 2, [-0.32, 0.32], color="#1d3557", lw=1.2)
            axis.text(
                share * total,
                0.38,
                f"{share:.0%} of the way:\n{numbers[key]:.0f} °C",
                ha="center",
                va="bottom",
                fontsize=7.5,
            )
        hot_from = total * (1 - numbers["above"])
        axis.annotate(
            "",
            xy=(total, -0.42),
            xytext=(hot_from, -0.42),
            arrowprops={"arrowstyle": "<->", "color": "#1d3557", "lw": 1},
        )
        axis.text(
            (hot_from + total) / 2,
            -0.5,
            f"{numbers['above']:.0%} of the people are above {self.THRESHOLD:.0f} °C",
            ha="center",
            va="top",
            fontsize=7.5,
        )
        axis.set_xlim(0, total)
        axis.set_ylim(-0.85, 0.95)
        axis.set_yticks([])
        axis.set_xticks(
            [0, 250_000, 500_000, 750_000, 1_000_000],
            ["0", "250,000", "500,000", "750,000", "1,000,000"],
        )
        axis.set_xlabel("people")
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.set_title(
            "its people, lined up from the coolest cell to the hottest: "
            "the 10th, 50th and 90th percentiles",
            fontsize=9,
        )

    @staticmethod
    def compact(data: str) -> str:
        """An SVG path's data, rounded to a tenth of a pixel and written compactly.

        In a path without curves, the line-to command "L" is dropped: in SVG, pairs of
        numbers after a move-to or a line-to continue as lines.
        """
        data = re.sub(r"(\d+\.\d)\d+", r"\1", data)
        if not re.search(r"[CcQqSsTtAa]", data):
            data = re.sub(r"\s*L\s*", " ", data)
        return re.sub(r"\s+", " ", data).strip()


def main() -> None:
    example = WorkedExample()
    print(example.numbers())
    example.draw(FIGURE)
    print(f"wrote {FIGURE.name}")


if __name__ == "__main__":
    main()

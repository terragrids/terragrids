"""The population weighting on real data: five countries on one heatwave day.

The study builds the library's population weights for the areas of Germany, France,
the United Kingdom, Italy and Spain at the level health data is published (NUTS-3, or
its national equivalent), and for two sets of large areas: England's nine regions and
Germany as a whole. It applies them to the daily maximum 2 m temperature of 1 July 2025
from ERA5, the day the heatwave of that summer reached all five countries' capitals. It
records:

- how long building and applying the weights takes, and the memory they use;
- each area's population from the weights, against the official count;
- the share of each area's people that its value covers, with Spain run twice: on the
  main European grid alone, and with the Canary Islands' own grid;
- how far each area's population-weighted mean lies from the plain average of the
  cells whose centres lie inside it, where every cell counts once;
- for the large areas, the spread of temperatures among their people.

The results are written to ``aggregation_study_2025_07_01.json`` and the maps to
``figures/``, both next to this file.

Run it from the repository's root, with the study's extra tools installed::

    pip install -e ".[study]"
    python docs/methodology/aggregation_study.py --data <a folder for the downloads>

ERA5 comes from the Copernicus Climate Data Store, which needs a free account and its
key in ``~/.cdsapirc``. Every other input downloads without an account. The downloads
are kept in the data folder and reused on the next run; none of them is part of the
library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import tracemalloc
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray

from terragrids.areas import NO_CELL, AreaWeights

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "aggregation_study_2025_07_01.json"
FIGURES = HERE / "figures"

DAY = "2025-07-01"
# A day's maximum above this counts as hot, for the share of people above it.
HOT_C = 30.0
# Copernicus cuts ERA5 to rectangles: north, west, south and east edges, in degrees.
MAIN = (72.0, -25.0, 34.0, 45.0)
CANARIES = (30.0, -19.0, 27.0, -13.0)


class Downloads:
    """The study's inputs, each fetched once into a data folder and reused after.

    Each input's address and SHA-256 fingerprint are recorded, so the results say
    exactly which files they came from.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.used: dict[str, dict[str, Any]] = {}

    def fetch(self, name: str, url: str) -> Path:
        """The input saved as ``name``, downloaded from ``url`` if it is not there yet."""
        path = self.folder / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(path.name + ".partial")
            request = urllib.request.Request(url, headers={"User-Agent": "terragrids-study"})
            with urllib.request.urlopen(request, timeout=600) as response:
                with partial.open("wb") as stream:
                    shutil.copyfileobj(response, stream, length=1 << 20)
            partial.rename(path)
        self.record(name, path, url)
        return path

    def record(self, name: str, path: Path, source: str) -> None:
        """Notes an input's source and fingerprint for the results."""
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        self.used[name] = {"source": source, "sha256": digest.hexdigest()}


class Era5Day:
    """One day's maximum 2 m temperature on an ERA5 rectangle, in °C."""

    def __init__(self, downloads: Downloads, name: str, rectangle: tuple[float, ...]) -> None:
        self.downloads = downloads
        self.name = name
        self.rectangle = rectangle

    def load(self) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
        """The grid's latitudes and longitudes, and each cell's maximum over the day."""
        import xarray as xr

        path = self.downloads.folder / f"era5_t2m_{DAY}_{self.name}.nc"
        if not path.exists():
            self.retrieve(path)
        self.downloads.record(path.name, path, "ERA5 hourly on single levels, Copernicus CDS")
        with xr.open_dataset(path) as data:
            daily_max = data["t2m"].max("valid_time").values - 273.15
            return data["latitude"].values, data["longitude"].values, daily_max

    def retrieve(self, path: Path) -> None:
        """Downloads the day's 24 hourly temperatures over the rectangle."""
        import cdsapi

        year, month, day = DAY.split("-")
        request = {
            "product_type": ["reanalysis"],
            "variable": ["2m_temperature"],
            "year": [year],
            "month": [month],
            "day": [day],
            "time": [f"{hour:02d}:00" for hour in range(24)],
            "area": list(self.rectangle),
            "data_format": "netcdf",
            "download_format": "unarchived",
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        cdsapi.Client(quiet=True).retrieve("reanalysis-era5-single-levels", request, str(path))


class Population:
    """GHS-POP's people counts, one point per square of its grid, for a set of areas."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def points(
        self, areas: Areas
    ) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
        """The latitude, longitude and count of every populated square near the areas.

        It reads one window of the grid around each group of neighbouring areas, so a
        country with distant islands or overseas regions is not read as one huge
        rectangle.
        """
        import rasterio
        from rasterio.windows import from_bounds

        indices, counts = [], []
        with rasterio.open(f"zip://{self.path}!{self.tiff_name()}") as raster:
            for west, south, east, north in self.clusters(areas):
                window = from_bounds(west, south, east, north, raster.transform)
                window = window.round_offsets().round_lengths()
                block = raster.read(1, window=window, masked=True).filled(0)
                rows, columns = np.nonzero(block > 0)
                index = (rows + window.row_off) * raster.width + columns + window.col_off
                indices.append(index)
                counts.append(block[rows, columns].astype(float))
            transform, width = raster.transform, raster.width
        # A square inside two windows is kept once.
        unique, first = np.unique(np.concatenate(indices), return_index=True)
        rows, columns = np.divmod(unique, width)
        lon, lat = rasterio.transform.xy(transform, rows, columns, offset="center")
        return np.asarray(lat), np.asarray(lon), np.concatenate(counts)[first]

    @staticmethod
    def clusters(areas: Areas, margin: float = 0.5) -> list[tuple[float, float, float, float]]:
        """Rectangles around groups of areas: those within ``margin`` degrees are joined."""
        boxes = [tuple(shapely.bounds(boundary)) for boundary in areas.boundaries.values()]
        joined = True
        while joined:
            joined = False
            merged: list[tuple[float, float, float, float]] = []
            for box in boxes:
                for position, other in enumerate(merged):
                    if (
                        box[0] <= other[2] + margin
                        and other[0] <= box[2] + margin
                        and box[1] <= other[3] + margin
                        and other[1] <= box[3] + margin
                    ):
                        merged[position] = (
                            min(box[0], other[0]),
                            min(box[1], other[1]),
                            max(box[2], other[2]),
                            max(box[3], other[3]),
                        )
                        joined = True
                        break
                else:
                    merged.append(box)
            boxes = merged
        return [(w - 0.01, s - 0.01, e + 0.01, n + 0.01) for w, s, e, n in boxes]

    def tiff_name(self) -> str:
        """The GeoTIFF's name inside the zip."""
        import zipfile

        with zipfile.ZipFile(self.path) as archive:
            return next(name for name in archive.namelist() if name.endswith(".tif"))


@dataclass
class Measured:
    """How long a step took, and the most memory it held at once."""

    seconds: float = 0.0
    peak_mb: float = 0.0


class Meter:
    """Measures a step's time and its peak memory, in two separate runs.

    Python's memory tracer slows the code it watches, so the time comes from a run
    without it, and the memory from a second run with it.
    """

    @staticmethod
    def measure(step: Callable[[], Any]) -> tuple[Any, Measured]:
        """The step's result, and its time and peak memory."""
        started = time.perf_counter()
        result = step()
        seconds = time.perf_counter() - started
        with Meter.traced() as peak:
            step()
        return result, Measured(seconds=round(seconds, 2), peak_mb=round(peak[0] / 2**20, 1))

    @staticmethod
    @contextmanager
    def traced() -> Iterator[list[int]]:
        """Yields a list that holds the peak traced memory, in bytes, once the block ends."""
        peak = [0]
        tracemalloc.start()
        try:
            yield peak
        finally:
            peak[0] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()


@dataclass
class Areas:
    """A set of areas: each code's boundary and name, in longitude and latitude."""

    boundaries: dict[str, shapely.Geometry]
    names: dict[str, str] = field(default_factory=dict)
    regions: dict[str, str] = field(default_factory=dict)
    """The region each area belongs to, where the source gives it."""

    def bounds(self, margin: float = 0.1) -> tuple[float, float, float, float]:
        """The rectangle around every area, with a margin: west, south, east, north."""
        west, south, east, north = shapely.total_bounds(list(self.boundaries.values()))
        return west - margin, south - margin, east + margin, north + margin


class Run:
    """Builds one set of areas' weights on one grid, applies them and measures both."""

    def __init__(
        self,
        areas: Areas,
        points: tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]],
        grid: tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]],
    ) -> None:
        self.areas = areas
        self.points = points
        self.lat, self.lon, self.values = grid

    def weights(self) -> tuple[AreaWeights, Measured]:
        """The areas' weights on the grid, and what building them took."""
        lat, lon, count = self.points

        def build() -> AreaWeights:
            return AreaWeights.from_population(
                self.areas.boundaries, lat, lon, count, self.lat, self.lon
            )

        weights, cost = Meter.measure(build)
        return weights, cost

    def apply(self, weights: AreaWeights) -> tuple[dict[str, NDArray[np.float64]], Measured]:
        """The mean, covered share, spread and hot share per area, and what applying took."""

        def summarise() -> dict[str, NDArray[np.float64]]:
            values = weights.apply(self.values)
            return {
                "mean": values.mean,
                "covered": values.covered,
                "spread": weights.percentiles(self.values, (10.0, 50.0, 90.0)),
                "hot": weights.share_above(self.values, HOT_C),
            }

        summary, cost = Meter.measure(summarise)
        return summary, cost

    def plain_means(self, weights: AreaWeights) -> NDArray[np.float64]:
        """Each area's plain average over the grid cells whose centres lie inside it.

        Each cell counts once, however many people live in it, for comparison with the
        population-weighted mean. An area that contains no cell centre gets NaN.
        """
        lon, lat = np.meshgrid(self.lon, self.lat)
        means = np.full(len(weights.codes), np.nan)
        for position, code in enumerate(weights.codes):
            boundary = self.areas.boundaries[code]
            inside = shapely.contains_xy(boundary, lon, lat)
            if inside.any():
                means[position] = float(np.nanmean(self.values[inside]))
        return means

    def counted_twice(self, weights: AreaWeights) -> float:
        """The share of people counted in more than one area, from points on borders.

        It compares the sum of the areas' populations with the people inside their union.
        """
        lat, lon, count = self.points
        union = shapely.union_all(list(self.areas.boundaries.values()))
        shapely.prepare(union)
        inside = float(count[shapely.intersects_xy(union, lon, lat)].sum())
        return float(weights.population.sum() / inside - 1.0)


class Maps:
    """The study's maps, as SVG files: text, with no date and no drawing tool's version.

    Boundaries are simplified to about a pixel, and the drawing's coordinates rounded to
    a tenth of a pixel, so that each file stays small enough to ship with the library.
    """

    # The colour of an area that has no value.
    MISSING = "#d4d4d4"
    # The charts' two colours: a range of people, and their average.
    BAR = "#f4a261"
    MARK = "#264653"
    # Temperatures run from blue for cool to red for hot.
    HEAT = "RdYlBu_r"

    def __init__(self, folder: Path, credit: str) -> None:
        import matplotlib

        matplotlib.use("svg")
        matplotlib.rcParams.update({"svg.hashsalt": "terragrids", "svg.fonttype": "none"})
        matplotlib.rcParams.update({"font.size": 8, "axes.titlesize": 9})
        self.folder = folder
        self.credit = credit

    def areas(
        self,
        name: str,
        countries: list[tuple[Areas, Mapping[str, float]]],
        title: str,
        label: str,
        colours: str,
        limits: tuple[float, float],
    ) -> Path:
        """One map of every country's areas, each area coloured by its value.

        An area without a value is drawn in light grey, and each country's outline is
        drawn over its areas.
        """
        import matplotlib.pyplot as plt
        from matplotlib.collections import PathCollection
        from matplotlib.patches import PathPatch

        every = [boundary for areas, _ in countries for boundary in areas.boundaries.values()]
        west, south, east, north = shapely.total_bounds(every)
        tolerance = max(east - west, north - south) / 500
        figure, axis = plt.subplots(figsize=(7.0, 7.4))
        scale = plt.Normalize(*limits)
        palette = plt.get_cmap(colours).with_extremes(bad=self.MISSING)
        for areas, values in countries:
            codes = list(areas.boundaries)
            paths = [self.path(areas.boundaries[code], tolerance) for code in codes]
            shading = np.ma.masked_invalid([values.get(code, np.nan) for code in codes])
            collection = PathCollection(paths, cmap=palette, norm=scale, linewidths=0.15)
            collection.set_array(shading)
            collection.set_edgecolor("white")
            axis.add_collection(collection)
            country = shapely.union_all(list(areas.boundaries.values()))
            axis.add_patch(
                PathPatch(self.path(country, tolerance), fill=False, lw=0.5, edgecolor="#404040")
            )
        axis.set_xlim(west - 0.3, east + 0.3)
        axis.set_ylim(south - 0.3, north + 0.3)
        # Degrees of longitude shrink towards the poles; this keeps shapes true.
        axis.set_aspect(1 / np.cos(np.deg2rad((south + north) / 2)))
        axis.set_axis_off()
        axis.set_title(title, fontsize=11)
        bar = figure.colorbar(
            plt.cm.ScalarMappable(norm=scale, cmap=palette),
            ax=axis,
            orientation="horizontal",
            fraction=0.035,
            pad=0.02,
            aspect=40,
        )
        bar.set_label(label)
        figure.subplots_adjust(left=0.02, right=0.98, top=0.95, bottom=0.08)
        return self.save(figure, name)

    def save(self, figure: Any, name: str) -> Path:
        """Writes the figure with its credit, without a date or version, then closes it."""
        import re

        import matplotlib.pyplot as plt

        figure.text(0.01, 0.005, self.credit, fontsize=5, wrap=True, va="bottom")
        path = self.folder / f"{name}.svg"
        self.folder.mkdir(parents=True, exist_ok=True)
        figure.savefig(path, format="svg", metadata={"Date": None, "Creator": None})
        plt.close(figure)
        text = re.sub(r'd="([^"]*)"', lambda m: f'd="{self.compact(m.group(1))}"', path.read_text())
        path.write_text(text)
        return path

    @staticmethod
    def compact(data: str) -> str:
        """An SVG path's data, rounded to a tenth of a pixel and written compactly.

        In a path without curves, the line-to command "L" is dropped: in SVG, pairs of
        numbers after a move-to or a line-to continue as lines.
        """
        import re

        data = re.sub(r"(\d+\.\d)\d+", r"\1", data)
        if not re.search(r"[CcQqSsTtAa]", data):
            data = re.sub(r"\s*L\s*", " ", data)
        return re.sub(r"\s+", " ", data).strip()

    def spread(self, name: str, rows: list[dict[str, Any]], title: str) -> Path:
        """Each large area's range from its 10th to its 90th percentile, with its mean."""
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D

        figure, axis = plt.subplots(figsize=(7.0, 0.42 * len(rows) + 1.9))
        for position, row in enumerate(rows):
            axis.plot(
                [row["p10_c"], row["p90_c"]],
                [position, position],
                lw=7,
                color=self.BAR,
                solid_capstyle="butt",
            )
            axis.plot(row["mean_c"], position, "o", color=self.MARK, ms=6)
            axis.text(row["p90_c"] + 0.4, position, self.hot_share(row), va="center", fontsize=7.5)
        axis.set_yticks(range(len(rows)), [row["name"] for row in rows])
        axis.invert_yaxis()
        axis.axvline(HOT_C, color="#999999", lw=0.8, ls="--", zorder=0)
        # A row of room above the first area, for the threshold's label.
        axis.set_ylim(len(rows) - 0.4, -1.1)
        axis.text(HOT_C, -1.0, f" {HOT_C:.0f} °C", color="#777777", va="top", fontsize=7.5)
        lowest = min(row["p10_c"] for row in rows)
        highest = max(row["p90_c"] for row in rows)
        axis.set_xlim(lowest - 1, highest + 9)
        axis.set_xlabel("the day's highest temperature where people live (°C)")
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_title(title, fontsize=10, loc="left")
        axis.legend(
            handles=[
                Line2D([], [], lw=7, color=self.BAR, solid_capstyle="butt"),
                Line2D([], [], ls="", marker="o", color=self.MARK, ms=6),
            ],
            labels=[
                "where the middle 80% of its people were: from the 10th to the 90th percentile",
                "the average over its people (the population-weighted mean)",
            ],
            loc="upper center",
            bbox_to_anchor=(0.4, -0.12 - 0.6 / len(rows)),
            frameon=False,
        )
        figure.subplots_adjust(left=0.24, right=0.98, top=0.88, bottom=0.27)
        return self.save(figure, name)

    @staticmethod
    def hot_share(row: Mapping[str, Any]) -> str:
        """The share of an area's people above the hot threshold, in words."""
        share = row[f"share_above_{HOT_C:.0f}_c"]
        if share == 0:
            return f"none of its people above {HOT_C:.0f} °C"
        if share < 0.005:
            return f"under 1% of its people above {HOT_C:.0f} °C"
        return f"{share:.0%} of its people above {HOT_C:.0f} °C"

    def cells(
        self,
        name: str,
        title: str,
        boundary: shapely.Geometry,
        grid: tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]],
        people: NDArray[np.float64],
        row: dict[str, Any],
        neighbours: Mapping[str, shapely.Geometry],
    ) -> Path:
        """One area's cells coloured by value, beside its people at each temperature.

        Only the cells where the area's people live are drawn, and only the part of the
        grid around the area is written, which keeps the file small. A neighbouring
        area whose centre falls inside the drawing is named, so a gap is explained.
        """
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
        from matplotlib.patches import PathPatch

        lat, lon, values = grid
        counts = people.reshape(values.shape)
        lived = counts > 0
        rows, columns = np.nonzero(lived)
        near = (
            slice(max(rows.min() - 1, 0), rows.max() + 2),
            slice(max(columns.min() - 1, 0), columns.max() + 2),
        )
        shown = np.ma.masked_where(~lived[near], values[near])
        figure, (left, right) = plt.subplots(1, 2, figsize=(7.6, 3.9), width_ratios=[1.15, 1])
        image = left.pcolormesh(
            lon[near[1]], lat[near[0]], shown, cmap=self.HEAT, shading="nearest"
        )
        west, south, east, north = shapely.bounds(boundary)
        step = abs(float(lat[1] - lat[0]))
        outline = self.path(boundary, (east - west) / 300)
        left.add_patch(PathPatch(outline, fill=False, lw=0.9, edgecolor="#1d3557"))
        left.set_xlim(west - step, east + step)
        left.set_ylim(south - step, north + step)
        for other, shape in neighbours.items():
            centre = shapely.point_on_surface(shape)
            if shapely.contains_xy(boundary.envelope, centre.x, centre.y):
                left.text(
                    centre.x,
                    centre.y,
                    other,
                    ha="center",
                    va="center",
                    fontsize=7,
                    style="italic",
                    color="#333333",
                    bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "none", "alpha": 0.85},
                )
        left.set_aspect(1 / np.cos(np.deg2rad((south + north) / 2)))
        left.set_axis_off()
        left.set_title("each square is one weather cell\nwhere some of its people live", fontsize=8)
        figure.colorbar(image, ax=left, shrink=0.75, label="the day's highest temperature (°C)")

        temperatures = values[lived]
        bins = np.arange(np.floor(temperatures.min()), np.ceil(temperatures.max()) + 0.5, 0.5)
        right.hist(temperatures, bins=bins, weights=counts[lived] / 1e6, color=self.BAR)
        lines = [
            ("the 10th percentile: a tenth of its people were cooler", row["p10_c"], ":"),
            ("the average over its people", row["mean_c"], "-"),
            ("the 90th percentile: a tenth of its people were hotter", row["p90_c"], "--"),
        ]
        for _, value, style in lines:
            right.axvline(value, color=self.MARK, ls=style, lw=1.1)
        right.set_xlabel("the day's highest temperature (°C), in steps of 0.5 °C")
        right.set_ylabel("people (millions)")
        right.spines[["top", "right"]].set_visible(False)
        right.set_title("how many of its people were at each temperature", fontsize=8)
        figure.legend(
            handles=[Line2D([], [], color=self.MARK, ls=style, lw=1.1) for _, _, style in lines],
            labels=[label for label, _, _ in lines],
            loc="lower center",
            bbox_to_anchor=(0.5, 0.04),
            ncols=1,
            frameon=False,
        )
        figure.suptitle(title, fontsize=10)
        figure.subplots_adjust(left=0.02, right=0.98, bottom=0.36, top=0.82, wspace=0.3)
        return self.save(figure, name)

    def grid(
        self,
        name: str,
        grid: tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]],
        used: NDArray[np.bool_],
        outlines: list[shapely.Geometry],
        closer: tuple[shapely.Geometry, NDArray[np.float64]],
    ) -> Path:
        """The weather grid: every cell in use, and a closer look at one area's cells.

        On the left, each cell where the countries' people live is drawn as its square,
        coloured by its value in steps of 0.5 °C. The squares of one step are joined into
        one shape, which keeps the file small. On the right, the cells around one area
        are drawn one by one, with a dot on each cell sized by the area's people in it.
        """
        import matplotlib.pyplot as plt
        from matplotlib.colors import BoundaryNorm
        from matplotlib.patches import PathPatch, Rectangle

        lat, lon, values = grid
        half_lat, half_lon = abs(lat[1] - lat[0]) / 2, abs(lon[1] - lon[0]) / 2
        steps = np.arange(15.0, 40.5, 0.5)
        palette = plt.get_cmap(self.HEAT)
        scale = BoundaryNorm(steps, palette.N, extend="both")
        figure, (left, right) = plt.subplots(1, 2, figsize=(7.6, 5.0), width_ratios=[1.25, 1])

        rows, columns = np.nonzero(used)
        step_of = np.digitize(values[rows, columns], steps)
        for step in np.unique(step_of):
            mine = step_of == step
            squares = shapely.box(
                lon[columns[mine]] - half_lon,
                lat[rows[mine]] - half_lat,
                lon[columns[mine]] + half_lon,
                lat[rows[mine]] + half_lat,
            )
            colour = palette(scale(float(values[rows[mine][0], columns[mine][0]])))
            shape = self.path(shapely.union_all(squares), 0.0)
            left.add_patch(PathPatch(shape, fc=colour, ec=colour, lw=0.2))
        west, south, east, north = shapely.total_bounds(outlines)
        tolerance = max(east - west, north - south) / 400
        for outline in outlines:
            left.add_patch(
                PathPatch(self.path(outline, tolerance), fill=False, lw=0.4, edgecolor="#303030")
            )
        boundary, people = closer
        near = shapely.bounds(shapely.buffer(boundary, 2 * half_lon))
        left.add_patch(
            Rectangle(
                (near[0], near[1]),
                near[2] - near[0],
                near[3] - near[1],
                fill=False,
                lw=1.0,
                edgecolor="#1d3557",
            )
        )
        left.set_xlim(west - 0.3, east + 0.3)
        left.set_ylim(south - 0.3, north + 0.3)
        left.set_aspect(1 / np.cos(np.deg2rad((south + north) / 2)))
        left.set_axis_off()
        left.set_title("every cell where the five countries' people live", fontsize=8)

        inside_lat = (lat >= near[1] - half_lat) & (lat <= near[3] + half_lat)
        inside_lon = (lon >= near[0] - half_lon) & (lon <= near[2] + half_lon)
        right.pcolormesh(
            lon[inside_lon],
            lat[inside_lat],
            values[np.ix_(inside_lat, inside_lon)],
            cmap=palette,
            norm=scale,
            shading="nearest",
            edgecolors="white",
            linewidth=0.6,
        )
        counts = people.reshape(values.shape)
        lived_rows, lived_columns = np.nonzero(counts)
        sizes = counts[lived_rows, lived_columns]
        right.scatter(
            lon[lived_columns],
            lat[lived_rows],
            s=sizes / 4_000,
            color=self.MARK,
            alpha=0.85,
            linewidths=0,
        )
        right.add_patch(
            PathPatch(self.path(boundary, 0.005), fill=False, lw=1.2, edgecolor=self.MARK)
        )
        # Only the part of each country's border inside the closer look is written.
        for outline in outlines:
            border = shapely.clip_by_rect(shapely.boundary(outline), *near)
            for line in shapely.get_parts(shapely.simplify(border, 0.005)):
                x, y = np.asarray(line.coords).T
                right.plot(x, y, lw=0.6, ls="--", color="#303030")
        right.set_xlim(near[0], near[2])
        right.set_ylim(near[1], near[3])
        right.set_aspect(1 / np.cos(np.deg2rad((near[1] + near[3]) / 2)))
        right.set_axis_off()
        right.set_title(
            "around Turin: each square is one cell;\neach dot, the people of the metropolitan "
            "city in it",
            fontsize=8,
        )
        for count, label in [(100_000, "100,000 people"), (1_000_000, "1,000,000 people")]:
            right.scatter([], [], s=count / 4_000, color=self.MARK, alpha=0.85, label=label)
        right.plot([], [], lw=1.2, color=self.MARK, label="Turin's boundary")
        right.plot([], [], lw=0.6, ls="--", color="#303030", label="border with France")
        right.legend(
            loc="upper center", bbox_to_anchor=(0.5, -0.03), ncols=2, frameon=False, fontsize=7.5
        )
        bar = figure.colorbar(
            plt.cm.ScalarMappable(norm=scale, cmap=palette),
            ax=left,
            orientation="horizontal",
            fraction=0.05,
            pad=0.03,
            aspect=30,
        )
        bar.set_label("each cell's highest temperature of the day (°C)")
        figure.suptitle("The grid: ERA5's cells, 0.25° across, on 1 July 2025", fontsize=10)
        figure.subplots_adjust(left=0.01, right=0.99, top=0.88, bottom=0.1, wspace=0.08)
        return self.save(figure, name)

    @staticmethod
    def path(boundary: shapely.Geometry, tolerance: float) -> Any:
        """A drawing path for a boundary, holes included, simplified to the tolerance.

        Islands and holes smaller than a square of the tolerance's side are left out,
        except the boundary's largest part. Joining areas into a country leaves many
        such holes, from tiny gaps between their borders, and they would fill the file.
        """
        from matplotlib.path import Path as DrawingPath

        simple = shapely.simplify(boundary, tolerance, preserve_topology=True)
        smallest = tolerance**2
        parts = sorted(shapely.get_parts(simple), key=lambda part: -part.area)
        vertices: list[NDArray[np.float64]] = []
        codes: list[NDArray[np.uint8]] = []
        for rank, polygon in enumerate(parts):
            if rank > 0 and polygon.area < smallest:
                break
            # Outlines run anticlockwise and holes clockwise, so the holes stay empty.
            oriented = shapely.geometry.polygon.orient(polygon, 1.0)
            holes = [r for r in oriented.interiors if shapely.Polygon(r).area >= smallest]
            for ring in [oriented.exterior, *holes]:
                points = np.asarray(ring.coords)
                ring_codes = np.full(len(points), DrawingPath.LINETO, dtype=np.uint8)
                ring_codes[0], ring_codes[-1] = DrawingPath.MOVETO, DrawingPath.CLOSEPOLY
                vertices.append(points)
                codes.append(ring_codes)
        if not vertices:
            return DrawingPath(np.zeros((0, 2)))
        return DrawingPath(np.concatenate(vertices), np.concatenate(codes))


class Summary:
    """Turns a run's arrays into the plain numbers the results file keeps."""

    @staticmethod
    def population(
        weights: AreaWeights, official: Mapping[str, float], groups: Mapping[str, list[str]]
    ) -> dict[str, Any]:
        """Each area's population from the weights, against the official count.

        ``groups`` joins areas whose official count is published only for their sum,
        such as islands that one official region covers together. Each key is the
        official region's code, and its value the area codes it covers.
        """
        ours = dict(zip(weights.codes, weights.population.tolist(), strict=True))
        pairs: dict[str, tuple[float, float]] = {}
        for code, people in ours.items():
            if code in official:
                pairs[code] = (people, official[code])
        for region, members in groups.items():
            if region in official and all(member in ours for member in members):
                pairs[region] = (sum(ours[member] for member in members), official[region])
        ratios = np.array([mine / theirs for mine, theirs in pairs.values()])
        worst = sorted(pairs, key=lambda code: abs(np.log(pairs[code][0] / pairs[code][1])))
        return {
            "areas_compared": len(pairs),
            "areas_without_an_official_count": sorted(
                set(ours) - set(pairs) - {m for ms in groups.values() for m in ms}
            ),
            "weights_total": round(sum(mine for mine, _ in pairs.values())),
            "official_total": round(sum(theirs for _, theirs in pairs.values())),
            "ratio": Summary.quantiles(ratios, 3),
            "furthest": [
                {"area": code, "weights": round(pairs[code][0]), "official": round(pairs[code][1])}
                for code in reversed(worst[-5:])
            ],
        }

    @staticmethod
    def quantiles(values: NDArray[np.float64], digits: int) -> dict[str, float]:
        """The smallest, the 5th, 50th and 95th percentiles, and the largest."""
        finite = values[np.isfinite(values)]
        if not len(finite):
            return {}
        points = np.percentile(finite, [0, 5, 50, 95, 100])
        names = ["min", "p5", "median", "p95", "max"]
        return dict(zip(names, np.round(points, digits).tolist(), strict=True))

    @staticmethod
    def per_area(
        weights: AreaWeights,
        summary: Mapping[str, NDArray[np.float64]],
        plain: NDArray[np.float64] | None = None,
    ) -> dict:
        """Every area's mean, covered share, spread and hot share, rounded.

        With ``plain``, each row also has the area's plain average over its cells.
        """
        rows = {}
        for position, code in enumerate(weights.codes):
            p10, p50, p90 = summary["spread"][position].tolist()
            rows[code] = {
                "mean_c": Summary.rounded(summary["mean"][position], 2),
                "covered": Summary.rounded(summary["covered"][position], 4),
                "p10_c": Summary.rounded(p10, 2),
                "p50_c": Summary.rounded(p50, 2),
                "p90_c": Summary.rounded(p90, 2),
                f"share_above_{HOT_C:.0f}_c": Summary.rounded(summary["hot"][position], 4),
            }
            if plain is not None:
                rows[code]["plain_mean_c"] = Summary.rounded(plain[position], 2)
        return rows

    @staticmethod
    def difference(
        areas: Areas,
        weights: AreaWeights,
        summary: Mapping[str, NDArray[np.float64]],
        plain: NDArray[np.float64],
    ) -> dict[str, Any]:
        """How far the population-weighted mean lies from the plain average of the cells.

        Only the areas whose people the grid covers count. An area that holds no cell's
        centre has no plain average, so it is counted apart.
        """
        on_grid = summary["covered"] > 0.5
        gap = summary["mean"] - plain
        with_both = on_grid & np.isfinite(gap)
        order = np.argsort(-np.abs(np.where(with_both, gap, 0.0)))[:5]
        return {
            "areas_on_the_grid": int(on_grid.sum()),
            "areas_without_a_cell_centre": int((on_grid & np.isnan(plain)).sum()),
            "weighted_minus_plain_c": Summary.quantiles(gap[with_both], 2),
            "areas_differing_by_more_than_1_c": int((np.abs(gap[with_both]) > 1).sum()),
            "areas_differing_by_more_than_2_c": int((np.abs(gap[with_both]) > 2).sum()),
            "largest": [
                {
                    "area": weights.codes[position],
                    "name": areas.names.get(weights.codes[position], ""),
                    "weighted_c": Summary.rounded(summary["mean"][position], 2),
                    "plain_c": Summary.rounded(plain[position], 2),
                }
                for position in order
            ],
        }

    @staticmethod
    def rounded(value: float, digits: int) -> float | None:
        """A number rounded for the results file, or None where it is NaN."""
        return None if not np.isfinite(value) else round(float(value), digits)


class Sources:
    """Where each input comes from, and the credit its licence asks for."""

    ARCGIS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
    GEOJSON_QUERY = "query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
    GHSL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A"

    POPULATION = (
        f"{GHSL}/GHS_POP_E2025_GLOBE_R2023A_4326_30ss/V1-0/"
        "GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.zip"
    )
    GERMANY = (
        "https://sgx.geodatenzentrum.de/wfs_vg250?service=WFS&version=2.0.0&request=GetFeature"
        "&typeNames=vg250:vg250_krs&outputFormat=application/json&srsName=EPSG:4326"
    )
    FRANCE = (
        "https://data.geopf.fr/telechargement/download/ADMIN-EXPRESS-COG-CARTO/"
        "ADMIN-EXPRESS-COG-CARTO_4-0__FLATGEOBUF_WGS84G_FRA_2026-01-01/departement.fgb"
    )
    UK_ITL3 = f"{ARCGIS}/ITL3_JAN_2025_UK_BGC_V2/FeatureServer/1/{GEOJSON_QUERY}"
    ENGLAND_REGIONS = (
        f"{ARCGIS}/Regions_December_2024_Boundaries_EN_BGC/FeatureServer/0/{GEOJSON_QUERY}"
    )
    ITALY = (
        "https://www.istat.it/storage/cartografia/confini_amministrativi/generalizzati/2026/"
        "Limiti01012026_g.zip"
    )
    ITALY_LAYER = "ProvCM01012026_g/ProvCM01012026_g_WGS84.shp"
    SPAIN_FILTER = (
        '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" '
        'xmlns:au="http://inspire.ec.europa.eu/schemas/au/4.0" '
        'xmlns:xlink="http://www.w3.org/1999/xlink"><fes:PropertyIsEqualTo>'
        "<fes:ValueReference>au:nationalLevel/@xlink:href</fes:ValueReference>"
        "<fes:Literal>https://inspire.ec.europa.eu/codelist/AdministrativeHierarchyLevel/3rdOrder"
        "</fes:Literal></fes:PropertyIsEqualTo></fes:Filter>"
    )
    EUROSTAT = (
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/demo_r_pjanaggr3"
        "?format=JSON&lang=EN&sex=T&age=TOTAL&geoLevel=nuts3&lastTimePeriod=1"
    )
    NOMIS = (
        "https://www.nomisweb.co.uk/api/v01/dataset/NM_2002_1.data.csv?geography={geography}"
        "&date=2024&gender=0&c_age=200&measures=20100"
        "&select=date_name,geography_code,geography_name,obs_value"
    )
    LAD_TO_ITL3 = (
        f"{ARCGIS}/ITL125_ITL225_ITL325_LAU125_LAD24_UK_LU/FeatureServer/0/"
        "query?where=1%3D1&outFields=*&returnGeometry=false&f=json"
    )

    CREDITS = {
        "population": "GHS-POP R2023A, epoch 2025: Schiavina et al. (2023), European "
        "Commission, Joint Research Centre, doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE, "
        "CC BY 4.0",
        "temperature": "ERA5 hourly data on single levels: Hersbach et al. (2023), Copernicus "
        "Climate Change Service, doi:10.24381/cds.adbb2d47. Contains modified Copernicus "
        "Climate Change Service information 2025",
        "DE": "© GeoBasis-DE / BKG (2026), VG250 districts, dl-de/by-2-0; data changed",
        "FR": "IGN, ADMIN EXPRESS COG CARTO 4.0 (2026), Licence Ouverte / Etalab 2.0",
        "UK": "Source: Office for National Statistics, licensed under the Open Government "
        "Licence v3.0. Contains OS data © Crown copyright and database right 2025",
        "IT": "Istat, Confini delle unità amministrative a fini statistici al 1° gennaio "
        "2026, CC BY 4.0; data changed",
        "ES": "Obra derivada de Unidades administrativas (INSPIRE), CC BY 4.0, ign.es",
        "official populations": "Eurostat, demo_r_pjanaggr3, 1 January 2025; Office for "
        "National Statistics, mid-2024 estimates via Nomis, Open Government Licence v3.0. "
        "Where areas are summed, the sums are ours; Eurostat bears no responsibility for "
        "them",
    }
    MAP_CREDIT = (
        "Population: GHS-POP R2023A, European Commission JRC (CC BY 4.0). Temperature: ERA5, "
        "Copernicus Climate Change Service. Boundaries: © GeoBasis-DE / BKG (2026), "
        "dl-de/by-2-0, changed; IGN ADMIN EXPRESS (Etalab 2.0); ONS (OGL v3.0), contains OS "
        "data © Crown copyright 2025; Istat (CC BY 4.0), changed; obra derivada de IGN "
        "(CC BY 4.0), ign.es."
    )

    @classmethod
    def spain(cls) -> str:
        """IGN's provinces, through its WFS for administrative units, as GeoJSON."""
        from urllib.parse import urlencode

        query = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "au:AdministrativeUnit",
            "outputFormat": "application/geo+json",
            "srsName": "EPSG:4326",
            "FILTER": cls.SPAIN_FILTER,
        }
        return "https://www.ign.es/wfs-inspire/unidades-administrativas?" + urlencode(query)


class Boundaries:
    """Each country's areas, read from its own boundary source."""

    def __init__(self, downloads: Downloads) -> None:
        self.downloads = downloads

    def germany(self) -> Areas:
        """Germany's 400 districts (Kreise), labelled with their NUTS-3 codes."""
        features = self.geojson("germany/vg250_krs.geojson", Sources.GERMANY)
        # Land parts only: the same districts also come as parts over water.
        land = [f for f in features if f["properties"]["gf"] == 4]
        return self.areas(land, code="nuts", name="gen")

    def france(self) -> Areas:
        """France's 101 départements, by INSEE code, overseas ones included."""
        import geopandas

        path = self.downloads.fetch("france/departement.fgb", Sources.FRANCE)
        table = geopandas.read_file(path).to_crs(4326)
        return Areas(
            dict(zip(table["code_insee"], table.geometry, strict=True)),
            dict(zip(table["code_insee"], table["nom_officiel"], strict=True)),
        )

    def united_kingdom(self) -> Areas:
        """The UK's 182 ITL3 regions (January 2025)."""
        features = self.geojson("uk/itl3_2025.geojson", Sources.UK_ITL3)
        return self.areas(features, code="ITL325CD", name="ITL325NM")

    def england_regions(self) -> Areas:
        """England's nine regions (December 2024)."""
        features = self.geojson("uk/regions_2024.geojson", Sources.ENGLAND_REGIONS)
        return self.areas(features, code="RGN24CD", name="RGN24NM")

    def italy(self) -> Areas:
        """Italy's 110 provinces and metropolitan cities (1 January 2026), by ISTAT code."""
        import geopandas

        path = self.downloads.fetch("italy/Limiti01012026_g.zip", Sources.ITALY)
        # The names are UTF-8, which the file does not declare.
        layer = f"zip://{path}!{Sources.ITALY_LAYER}"
        table = geopandas.read_file(layer, encoding="utf-8").to_crs(4326)
        codes = [f"{code:03d}" for code in table["COD_UTS"]]
        return Areas(
            dict(zip(codes, table.geometry, strict=True)),
            dict(zip(codes, table["DEN_UTS"], strict=True)),
            {code: f"{region:02d}" for code, region in zip(codes, table["COD_REG"], strict=True)},
        )

    def spain(self) -> Areas:
        """Spain's 50 provinces and the cities of Ceuta and Melilla, by INE code."""
        features = self.geojson("spain/provinces.geojson", Sources.spain())
        boundaries, names = {}, {}
        for feature in features:
            # The national code is "34", the community's two digits, the province's two.
            code = feature["properties"]["nationalCode"][4:6]
            if code == "54":  # territory in no province
                continue
            boundaries[code] = shapely.geometry.shape(feature["geometry"])
            names[code] = self.spanish_name(feature["properties"]["name"])
        return Areas(boundaries, names)

    def geojson(self, name: str, url: str) -> list[dict[str, Any]]:
        """The features of a GeoJSON input."""
        path = self.downloads.fetch(name, url)
        features: list[dict[str, Any]] = json.loads(path.read_text())["features"]
        return features

    @staticmethod
    def areas(features: list[dict[str, Any]], code: str, name: str) -> Areas:
        """Areas from GeoJSON features, joining the parts that share a code."""
        parts: dict[str, list[shapely.Geometry]] = {}
        names: dict[str, str] = {}
        for feature in features:
            key = feature["properties"][code]
            parts.setdefault(key, []).append(shapely.geometry.shape(feature["geometry"]))
            names[key] = feature["properties"][name]
        boundaries = {key: shapely.union_all(shapes) for key, shapes in parts.items()}
        return Areas(boundaries, names)

    @staticmethod
    def spanish_name(name: Any) -> str:
        """A province's name from INSPIRE's nested name record."""
        if isinstance(name, str):
            name = json.loads(name)
        records = name if isinstance(name, list) else [name]
        spelling = records[0]["GeographicalName"]["spelling"]["SpellingOfName"]["text"]
        return str(spelling)


class Official:
    """The official population counts the areas' populations are checked against."""

    # Eurostat counts the Balearic and Canary Islands island by island. Their sums give
    # the provinces: the Balearic Islands (07), Las Palmas (35) and Santa Cruz de
    # Tenerife (38).
    SPAIN_ISLANDS = {
        "07": ["ES531", "ES532", "ES533"],
        "35": ["ES704", "ES705", "ES708"],
        "38": ["ES703", "ES706", "ES707", "ES709"],
    }
    # Sardinia's provinces changed in 2025, after NUTS 2024 fixed its five regions, so
    # it is compared as a whole: ISTAT's region 20 against Eurostat's ITG2.
    SARDINIA = ("20", "ITG2")

    def __init__(self, downloads: Downloads) -> None:
        self.downloads = downloads

    def eurostat(self) -> tuple[dict[str, float], dict[str, str]]:
        """Each NUTS-3 region's population on 1 January 2025, and its name."""
        path = self.downloads.fetch("official/eurostat_demo_r_pjanaggr3.json", Sources.EUROSTAT)
        data = json.loads(path.read_text())
        category = data["dimension"]["geo"]["category"]
        values = data["value"]
        counts = {
            code: float(values[str(index)])
            for code, index in category["index"].items()
            if str(index) in values
        }
        return counts, dict(category["label"])

    def united_kingdom(self) -> tuple[dict[str, float], dict[str, float]]:
        """Mid-2024 populations by ITL3 region, and by English region.

        Nomis gives local authorities, which a lookup places in ITL3 regions. An
        authority split between regions joins them: North Ayrshire lies partly in the
        Highlands and Islands (TLM20) and partly in North Ayrshire and East Ayrshire
        (TLM93). Joined regions get one count, keyed by their codes joined with "+",
        which holds the people of every authority in any of them.
        """
        authorities = self.nomis("official/nomis_lad_2024.csv", "TYPE424")
        regions = self.nomis("official/nomis_regions_2024.csv", "TYPE480")
        path = self.downloads.fetch("official/lad24_to_itl3.json", Sources.LAD_TO_ITL3)
        rows = [feature["attributes"] for feature in json.loads(path.read_text())["features"]]
        places: dict[str, set[str]] = {}
        for row in rows:
            places.setdefault(row["LAD24CD"], set()).add(row["ITL325CD"])
        joined: dict[str, set[str]] = {}
        for regions_of_one in places.values():
            group = set(regions_of_one).union(*(joined.get(r, set()) for r in regions_of_one))
            for region in group:
                joined[region] = group
        itl3: dict[str, float] = {}
        for authority, people in authorities.items():
            key = "+".join(sorted(joined[next(iter(places[authority]))]))
            itl3[key] = itl3.get(key, 0.0) + people
        return itl3, regions

    def nomis(self, name: str, geography: str) -> dict[str, float]:
        """Population by area code from a Nomis CSV."""
        import csv

        path = self.downloads.fetch(name, Sources.NOMIS.format(geography=geography))
        with path.open(newline="") as stream:
            return {
                row["GEOGRAPHY_CODE"]: float(row["OBS_VALUE"]) for row in csv.DictReader(stream)
            }

    @staticmethod
    def by_name(
        areas: Areas, counts: Mapping[str, float], labels: Mapping[str, str], prefix: str
    ) -> dict[str, float]:
        """Official counts for areas with national codes, matched to NUTS-3 by name."""
        nuts = {
            Official.key(part): code
            for code, label in labels.items()
            if code.startswith(prefix) and len(code) == 5 and code in counts
            for part in [label, *label.split("/")]
        }
        matched = {}
        for code, name in areas.names.items():
            for part in [name, *name.split("/")]:
                if Official.key(part) in nuts:
                    matched[code] = counts[nuts[Official.key(part)]]
                    break
        return matched

    @staticmethod
    def key(name: str) -> str:
        """A name reduced to lowercase letters and digits, for matching."""
        import unicodedata

        plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        return "".join(character for character in plain.lower() if character.isalnum())


class Study:
    """Runs the whole study, and writes its results and its maps."""

    COUNTRIES = {
        "DE": "Germany: districts (NUTS-3)",
        "FR": "France: départements (NUTS-3)",
        "UK": "United Kingdom: ITL3 regions",
        "IT": "Italy: provinces and metropolitan cities",
        "ES": "Spain: provinces",
    }
    # The metropolitan city of Turin, by its ISTAT code: the grid figure's closer look.
    TURIN = "201"

    def __init__(self, data: Path) -> None:
        self.downloads = Downloads(data)
        self.boundaries = Boundaries(self.downloads)
        self.official = Official(self.downloads)
        self.maps = Maps(FIGURES, Sources.MAP_CREDIT)

    def run(self) -> dict[str, Any]:
        """Every country's run, the large areas' spread, and the maps."""
        population = Population(
            self.downloads.fetch(
                "population/GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.zip", Sources.POPULATION
            )
        )
        europe = Era5Day(self.downloads, "europe", MAIN).load()
        canaries = Era5Day(self.downloads, "canaries", CANARIES).load()
        counts, labels = self.official.eurostat()
        itl3, regions = self.official.united_kingdom()
        countries = {
            "DE": self.boundaries.germany(),
            "FR": self.boundaries.france(),
            "UK": self.boundaries.united_kingdom(),
            "IT": self.boundaries.italy(),
            "ES": self.boundaries.spain(),
        }
        official = {
            "DE": (
                {code: counts[code] for code in countries["DE"].boundaries if code in counts},
                {},
            ),
            "FR": (Official.by_name(countries["FR"], counts, labels, "FR"), {}),
            "UK": self.united_kingdom_counts(itl3),
            "IT": self.italy_counts(countries["IT"], counts, labels),
            "ES": self.spain_counts(countries["ES"], counts, labels),
        }

        results: dict[str, Any] = {
            "day": DAY,
            "value": "each ERA5 cell's maximum of the day's 24 hourly 2 m temperatures "
            "(00-23 UTC), in °C",
            "hot_threshold_c": HOT_C,
            "grids": {
                "europe": self.grid_record(MAIN, europe),
                "canaries": self.grid_record(CANARIES, canaries),
            },
            "countries": {},
            "large_areas": {},
        }
        points_by_country, drawn, on_europe = {}, {}, {}
        for code, areas in countries.items():
            points = population.points(areas)
            points_by_country[code] = points
            run = Run(areas, points, europe)
            weights, built = run.weights()
            summary, applied = run.apply(weights)
            plain = run.plain_means(weights)
            on_europe[code] = dict(zip(weights.codes, summary["covered"].tolist(), strict=True))
            entry: dict[str, Any] = {
                "areas": self.COUNTRIES[code],
                "area_count": len(weights.codes),
                "population_points": int(len(points[0])),
                "weight_entries": int(len(weights.cell)),
                "build": vars(built),
                "apply": vars(applied),
                "population": Summary.population(weights, *official[code]),
                # Adding 0.0 turns rounding noise below zero (-0.0) into 0.0.
                "counted_in_two_areas": round(run.counted_twice(weights), 5) + 0.0,
                "covered_on_the_europe_grid": self.coverage(weights, summary),
                "against_the_plain_average": Summary.difference(areas, weights, summary, plain),
            }
            if code == "ES":
                second = Run(areas, points, canaries)
                island_weights, island_built = second.weights()
                island_summary, island_applied = second.apply(island_weights)
                summary = self.combine(summary, island_summary)
                entry["canaries_grid"] = {
                    "build": vars(island_built),
                    "apply": vars(island_applied),
                    "covered_with_both_grids": self.coverage(weights, summary),
                }
            entry["per_area"] = Summary.per_area(weights, summary, plain)
            results["countries"][code] = entry
            drawn[code] = (areas, weights, summary, plain)

        large, england = self.large_areas(countries, points_by_country, europe, regions, counts)
        results["large_areas"] = large
        results["inputs"] = self.downloads.used
        results["credits"] = Sources.CREDITS
        results["figures"] = self.draw(drawn, on_europe, large, england, europe)
        return results

    def large_areas(
        self,
        countries: Mapping[str, Areas],
        points: Mapping[str, Any],
        grid: tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]],
        regions: Mapping[str, float],
        counts: Mapping[str, float],
    ) -> tuple[dict[str, Any], tuple[Areas, AreaWeights]]:
        """England's nine regions and Germany as a whole: their spread on the day.

        Returns the results, and England's regions with their weights, for the maps.
        """
        england = self.boundaries.england_regions()
        germany = Areas(
            {"DE": shapely.union_all(list(countries["DE"].boundaries.values()))},
            {"DE": "Germany"},
        )
        germany_count = sum(value for code, value in counts.items() if code.startswith("DE"))
        out, kept = {}, {}
        for key, areas, base, official in [
            ("england_regions", england, points["UK"], dict(regions)),
            ("germany", germany, points["DE"], {"DE": germany_count}),
        ]:
            run = Run(areas, base, grid)
            weights, built = run.weights()
            summary, applied = run.apply(weights)
            rows = Summary.per_area(weights, summary)
            for code, row in rows.items():
                row["name"] = areas.names[code]
                row["p90_minus_p10_c"] = round(row["p90_c"] - row["p10_c"], 2)
            out[key] = {
                "build": vars(built),
                "apply": vars(applied),
                "population": Summary.population(weights, official, {}),
                "per_area": rows,
            }
            kept[key] = (areas, weights)
        return out, kept["england_regions"]

    def draw(
        self,
        drawn: Mapping[str, Any],
        on_europe: Mapping[str, Mapping[str, float]],
        large: Mapping[str, Any],
        england: tuple[Areas, AreaWeights],
        grid: tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]],
    ) -> list[str]:
        """The study's maps and charts; returns their file names."""
        means, gaps, outlines = [], [], []
        used = np.zeros(grid[2].size, dtype=bool)
        for code, (areas, weights, summary, plain) in drawn.items():
            # Only the areas on the Europe grid are drawn: the Canaries and France's
            # overseas regions would stretch the map across oceans.
            shown = Areas(
                {a: areas.boundaries[a] for a in weights.codes if on_europe[code][a] > 0.5},
                areas.names,
            )
            means.append((shown, dict(zip(weights.codes, summary["mean"].tolist(), strict=True))))
            difference = (summary["mean"] - plain).tolist()
            gaps.append((shown, dict(zip(weights.codes, difference, strict=True))))
            outlines.append(shapely.union_all(list(shown.boundaries.values())))
            used[weights.cell[weights.cell != NO_CELL]] = True
        # The grid itself: every cell where the five countries' people live, and a
        # closer look at one area, the metropolitan city of Turin.
        italy, italy_weights, _, _ = drawn["IT"]
        files = [
            self.maps.grid(
                "aggregation_grid_2025_07_01",
                grid,
                used.reshape(grid[2].shape),
                outlines,
                (italy.boundaries[self.TURIN], self.people_per_cell(italy_weights, self.TURIN)),
            ),
            self.maps.areas(
                "aggregation_mean_2025_07_01",
                means,
                "1 July 2025: how hot it was where each area's people live",
                "the day's highest temperature, averaged over each area's people (°C)",
                Maps.HEAT,
                (15.0, 40.0),
            ),
            self.maps.areas(
                "aggregation_weighted_minus_plain_2025_07_01",
                gaps,
                "1 July 2025: the people's average against the land's average",
                "the average over the area's people, minus the plain average of its cells (°C)\n"
                "red: its people were hotter than the plain average says · blue: cooler · "
                "grey: no plain average, as no cell's centre lies inside",
                "RdBu_r",
                (-3.0, 3.0),
            ),
        ]
        rows = sorted(large["england_regions"]["per_area"].values(), key=lambda r: -r["mean_c"])
        files.append(
            self.maps.spread(
                "aggregation_england_spread_2025_07_01",
                rows,
                "England's nine regions on 1 July 2025: how hot it was for their people",
            )
        )
        areas, weights = england
        widest = max(
            large["england_regions"]["per_area"].items(),
            key=lambda item: item[1]["p90_minus_p10_c"],
        )
        people = self.people_per_cell(weights, widest[0])
        files.append(
            self.maps.cells(
                "aggregation_region_cells_2025_07_01",
                f"England's {widest[1]['name']} on 1 July 2025: one region, many temperatures",
                areas.boundaries[widest[0]],
                grid,
                people,
                widest[1],
                {
                    areas.names[code]: shape
                    for code, shape in areas.boundaries.items()
                    if code != widest[0]
                },
            )
        )
        return [path.name for path in files]

    @staticmethod
    def people_per_cell(weights: AreaWeights, code: str) -> NDArray[np.float64]:
        """How many of one area's people live in each grid cell, as a flat array."""
        position = weights.codes.index(code)
        people = np.zeros(weights.shape[0] * weights.shape[1])
        mine = (weights.area == position) & (weights.cell != NO_CELL)
        np.add.at(people, weights.cell[mine], weights.people[mine])
        return people

    def coverage(self, weights: AreaWeights, summary: Mapping[str, NDArray[np.float64]]) -> dict:
        """How fully the areas' people are covered, and the areas covered least."""
        covered = dict(zip(weights.codes, summary["covered"].tolist(), strict=True))
        partial = {code: round(value, 4) for code, value in covered.items() if value < 0.9999}
        return {"share": Summary.quantiles(summary["covered"], 4), "below_1": partial}

    @staticmethod
    def combine(
        first: Mapping[str, NDArray[np.float64]], second: Mapping[str, NDArray[np.float64]]
    ) -> dict[str, NDArray[np.float64]]:
        """For each area, the run on whichever grid covers more of its people."""
        better = np.nan_to_num(second["covered"], nan=-1) > np.nan_to_num(first["covered"], nan=-1)
        combined = {}
        for key, value in first.items():
            pick = better[:, None] if value.ndim == 2 else better
            combined[key] = np.where(pick, second[key], value)
        return combined

    @staticmethod
    def grid_record(
        rectangle: tuple[float, ...],
        grid: tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]],
    ) -> dict[str, Any]:
        """A grid's rectangle and its size."""
        lat, lon, _ = grid
        return {"north_west_south_east": list(rectangle), "cells": [len(lat), len(lon)]}

    @staticmethod
    def united_kingdom_counts(itl3: Mapping[str, float]) -> tuple[dict[str, float], dict]:
        """ITL3 counts, with the regions that share a local authority joined."""
        groups = {key: key.split("+") for key in itl3 if "+" in key}
        return dict(itl3), groups

    @staticmethod
    def italy_counts(
        areas: Areas, counts: Mapping[str, float], labels: Mapping[str, str]
    ) -> tuple[dict[str, float], dict[str, list[str]]]:
        """Italian counts by name, with Sardinia compared as a whole."""
        region, nuts = Official.SARDINIA
        sardinia = [code for code, value in areas.regions.items() if value == region]
        others = Areas({}, {c: n for c, n in areas.names.items() if c not in sardinia})
        official = Official.by_name(others, counts, labels, "IT")
        official[nuts] = sum(v for c, v in counts.items() if c.startswith(nuts) and len(c) == 5)
        return official, {nuts: sardinia}

    @staticmethod
    def spain_counts(
        areas: Areas, counts: Mapping[str, float], labels: Mapping[str, str]
    ) -> tuple[dict[str, float], dict]:
        """Spanish counts by name, with the island provinces summed from their islands."""
        official = Official.by_name(areas, counts, labels, "ES")
        for province, islands in Official.SPAIN_ISLANDS.items():
            official[province] = sum(counts[island] for island in islands)
        return official, {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, required=True, help="the folder for downloads")
    arguments = parser.parse_args()
    results = Study(arguments.data).run()
    RESULTS.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {RESULTS.name} and {len(results['figures'])} figures")


if __name__ == "__main__":
    main()

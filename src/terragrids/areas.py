"""Population weights: who lives in which grid cell and which area.

Weather comes on a grid: ERA5's cells are 0.25° across, about 28 km by 18 km at 50°N.
Health data comes per administrative area: a region, a county, a city. To give each
area one value, the library weights each cell by the number of the area's people who
live in it. A cell where nobody lives counts for nothing.

The people come from a population grid, such as GHS-POP, the Global Human Settlement
Layer's population grid, which estimates how many people live in each square kilometre.
Each of its squares is passed as a *point*: the latitude and longitude of its centre,
and its count. :meth:`AreaWeights.from_population` places every point:

- **in a grid cell:** the cell whose centre is nearest to the point;
- **in an area:** every area whose boundary contains the point, or passes through it.

It then adds up the people of each cell–area pair. That is the slow part, so it runs once
for a set of areas and a grid, and its result, an :class:`AreaWeights`, can be saved and
loaded unchanged. The same weights then give the same values at every later use.

:meth:`AreaWeights.apply` then turns values on the grid, such as each cell's daily
maximum temperature, into one value per area. It takes the mean of the cells' values,
weighting each cell by the number of the area's people who live in it. For example, an
area with 900,000 people in a city cell at 36 °C and 100,000 in a rural cell at 30 °C
gets 35.4 °C, where the plain average of the two cells is 33 °C:

    (900,000 × 36 °C + 100,000 × 30 °C) / 1,000,000 = 35.4 °C

Alongside each mean, it gives the share of the area's people that the mean covers.
People in a cell without a value, or outside the grid, are left out of the mean, and the
share drops below 1. The same area, in three cases (``tests/test_areas.py`` checks
them):

===============================================  =======  ===========================
Case                                             Mean     Share of the people covered
===============================================  =======  ===========================
both cells have a value                          35.4 °C  1,000,000 / 1,000,000 = 1
the rural cell has no value that day             36 °C    900,000 / 1,000,000 = 0.9
50,000 more of its people live outside the grid  35.4 °C  1,000,000 / 1,050,000 ≈ 0.95
===============================================  =======  ===========================

In the second case the mean is the city's value alone, and it stands for nine in ten of
the area's people. In the third, the 50,000 people are counted in the area's population
but have no cell, so they lower the share without changing the mean. A share below 1
tells the reader that the mean leaves part of the area's people out. They can then
decide whether to use the value, mark it, or drop it.

A single mean can hide large differences inside an area, and the larger the area, the
more it can hide. :meth:`AreaWeights.percentiles` and :meth:`AreaWeights.share_above`
describe those differences. Take an area of four cells:

=======  =====  =======
Cell     Value  People
=======  =====  =======
hills    25 °C   50,000
coast    28 °C  150,000
suburbs  34 °C  300,000
city     37 °C  500,000
=======  =====  =======

Its mean is 34.15 °C. Now line up its 1,000,000 people from the coolest cell to the
hottest: the first 50,000 live in the hills, the next 150,000 on the coast, then
300,000 in the suburbs and 500,000 in the city. Then (``tests/test_areas.py`` checks
these values):

- the 10th percentile is 28 °C, because the 100,000th person lives on the coast;
- the 50th percentile is 34 °C, because the 500,000th person is the last in the suburbs;
- the 90th percentile is 37 °C, because the 900,000th person lives in the city;
- the share above 32 °C is 0.8: the 800,000 people in the suburbs and the city.

The mean alone hides that a fifth of the people had 28 °C or less, while half had
37 °C.

Three rules decide the edge cases:

- **A point outside the grid is kept.** It is counted in its area, with no cell. A grid
  cut to a rectangle, such as Europe's, may miss part of an area, and its people must
  not vanish from the area's total without trace.
- **Overlapping areas share their points.** A point inside two areas counts in both, and
  so does a point on the border between them.
- **A point with no people is skipped:** a count of zero, a negative count, or a missing
  one (NaN).

The grid may give its longitudes from -180 to 180 or from 0 to 360, and its latitudes in
either order, as ERA5 files do. The areas and the population points must use the same
longitudes as each other.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from os import PathLike

import numpy as np
import shapely
from numpy.typing import ArrayLike, NDArray

# The cell index of a point that no grid cell contains.
NO_CELL = -1
# The layout of the .npz files that AreaWeights.save writes. load refuses any other.
FORMAT_VERSION = 1
# How many values apply gathers at once: about 32 MB of floats.
_CHUNK = 4_000_000


@dataclass(frozen=True, eq=False)
class AreaWeights:
    """The people of each area, split by the grid cell they live in.

    Each entry is one pair of an area and a cell, with the number of the area's people
    living in that cell. The entries are sorted by area, then by cell. A cell of
    :data:`NO_CELL` holds the area's people who live outside the grid.
    """

    codes: tuple[str, ...]
    """The areas' codes, in the order the entries refer to them."""
    area: NDArray[np.intp]
    """For each entry, the position of its area in :attr:`codes`."""
    cell: NDArray[np.intp]
    """For each entry, its grid cell: ``row * len(lon) + column``, or :data:`NO_CELL`."""
    people: NDArray[np.float64]
    """For each entry, the number of people."""
    lat: NDArray[np.float64]
    """The latitudes of the grid's cell centres, as given."""
    lon: NDArray[np.float64]
    """The longitudes of the grid's cell centres, as given."""

    def __post_init__(self) -> None:
        if not len(self.area) == len(self.cell) == len(self.people):
            raise ValueError("area, cell and people differ in length")
        if np.any(np.diff(self.area) < 0):
            raise ValueError("the entries must be sorted by area")

    @classmethod
    def from_population(
        cls,
        areas: Mapping[str, shapely.Geometry],
        population_lat: ArrayLike,
        population_lon: ArrayLike,
        population: ArrayLike,
        lat: ArrayLike,
        lon: ArrayLike,
    ) -> AreaWeights:
        """The weights for a set of areas on a grid: who lives in which area and cell.

        ``areas`` maps each area's code to its boundary, a shapely polygon or multipolygon
        in longitude and latitude. ``population_lat``, ``population_lon`` and
        ``population`` give the population points: each point's centre, and the number
        of people there. ``lat`` and ``lon`` are the grid's cell centres, as
        one-dimensional arrays.
        """
        grid = _Grid(lat, lon)
        point_lat: NDArray[np.float64] = np.asarray(population_lat, dtype=float).ravel()
        point_lon: NDArray[np.float64] = np.asarray(population_lon, dtype=float).ravel()
        count: NDArray[np.float64] = np.asarray(population, dtype=float).ravel()
        if not len(point_lat) == len(point_lon) == len(count):
            raise ValueError("population_lat, population_lon and population differ in length")
        populated = count > 0
        point_lat, point_lon, count = point_lat[populated], point_lon[populated], count[populated]
        cell = grid.cells(point_lat, point_lon)

        codes = tuple(areas)
        pair_area, pair_cell, pair_people = [], [], []
        for position, code in enumerate(codes):
            boundary = areas[code]
            west, south, east, north = (float(edge) for edge in shapely.bounds(boundary))
            near = np.flatnonzero(
                (point_lon >= west)
                & (point_lon <= east)
                & (point_lat >= south)
                & (point_lat <= north)
            )
            shapely.prepare(boundary)
            inside = near[shapely.intersects_xy(boundary, point_lon[near], point_lat[near])]
            cells, which = np.unique(cell[inside], return_inverse=True)
            pair_area.append(np.full(len(cells), position, dtype=np.intp))
            pair_cell.append(cells.astype(np.intp))
            pair_people.append(np.bincount(which, weights=count[inside], minlength=len(cells)))

        return cls(
            codes=codes,
            area=np.concatenate(pair_area) if codes else np.empty(0, dtype=np.intp),
            cell=np.concatenate(pair_cell) if codes else np.empty(0, dtype=np.intp),
            people=np.concatenate(pair_people) if codes else np.empty(0),
            lat=grid.lat,
            lon=grid.lon,
        )

    @property
    def shape(self) -> tuple[int, int]:
        """The grid's shape: the number of latitudes, then of longitudes."""
        return len(self.lat), len(self.lon)

    @property
    def population(self) -> NDArray[np.float64]:
        """The number of people in each area, in the order of :attr:`codes`."""
        totals = np.bincount(self.area, weights=self.people, minlength=len(self.codes))
        return totals.astype(float)

    def apply(self, values: ArrayLike) -> AreaValues:
        """One value per area from values on the grid: their population-weighted mean.

        ``values`` holds one value per grid cell in its last two dimensions, laid out in
        the order the grid's ``lat`` and ``lon`` were given: its shape is
        ``(..., len(lat), len(lon))``. The dimensions before them, such as days, are
        kept, so values of shape ``(days, lat, lon)`` give means of shape
        ``(days, areas)``.

        A cell without a value (NaN) is left out of the mean, and so are the area's
        people outside the grid. :attr:`AreaValues.covered` gives the share of the area's
        people that the mean stands for. An area where nobody lives, or whose people
        all live where there is no value, gets NaN.
        """
        rows, leading = self._rows(values)
        weighted = np.empty((len(rows), len(self.codes)))
        counted = np.empty((len(rows), len(self.codes)))
        for part, picked, people in self._gather(rows):
            weighted[part] = self._per_area(np.where(people > 0, picked, 0.0) * people)
            counted[part] = self._per_area(people)
        with np.errstate(invalid="ignore"):
            mean = weighted / counted
            covered = counted / self.population
        shape = leading + (len(self.codes),)
        return AreaValues(
            codes=self.codes, mean=mean.reshape(shape), covered=covered.reshape(shape)
        )

    def percentiles(
        self, values: ArrayLike, q: Sequence[float] = (10.0, 50.0, 90.0)
    ) -> NDArray[np.float64]:
        """The value below which each share ``q`` (in %) of each area's people live.

        Line up the area's people from the coolest cell to the hottest. The 90th
        percentile is then the value of the cell where the person 90% of the way along
        lives: 90% of the people live in cells at that value or below. Each percentile is
        one cell's value, never a value between two cells.

        ``values`` is laid out as for :meth:`apply`. The result has one more dimension
        than the means, for ``q``: values of shape ``(days, lat, lon)`` give percentiles
        of shape ``(days, areas, len(q))``. Only the people that the mean covers count:
        those in cells without a value, or outside the grid, are left out. An area with
        none of its people covered gets NaN.
        """
        shares = np.asarray(q, dtype=float) / 100
        if shares.ndim != 1 or np.any((shares < 0) | (shares > 1)):
            raise ValueError("q must list percentages from 0 to 100")
        rows, leading = self._rows(values)
        result = np.full((len(rows), len(self.codes), len(shares)), np.nan)
        starts, counts = self._segments()
        for part, picked, people in self._gather(rows):
            for position in np.flatnonzero(counts):
                entries = slice(starts[position], starts[position] + counts[position])
                ranked, cumulative = self._line_up(picked[:, entries], people[:, entries])
                total = cumulative[:, -1:]
                # The first person at or past each share. The slack keeps rounding from
                # moving an exact share, such as 100,000 of 1,000,000, one cell up.
                reached = cumulative[:, :, None] >= total[:, :, None] * shares * (1 - 1e-12)
                chosen = np.take_along_axis(ranked, np.argmax(reached, axis=1), axis=1)
                result[part, position] = np.where(total > 0, chosen, np.nan)
        return result.reshape(leading + (len(self.codes), len(shares)))

    def share_above(self, values: ArrayLike, threshold: float) -> NDArray[np.float64]:
        """The share of each area's people living in cells whose value is above a threshold.

        A value equal to the threshold is not above it. ``values`` is laid out as for
        :meth:`apply`, and the result has the shape of the means. The share is of the
        people that the mean covers: those in cells without a value, or outside the
        grid, are left out. An area with none of its people covered gets NaN.
        """
        rows, leading = self._rows(values)
        above = np.empty((len(rows), len(self.codes)))
        counted = np.empty((len(rows), len(self.codes)))
        for part, picked, people in self._gather(rows):
            above[part] = self._per_area(np.where(picked > threshold, people, 0.0))
            counted[part] = self._per_area(people)
        with np.errstate(invalid="ignore"):
            share = above / counted
        return share.reshape(leading + (len(self.codes),))

    def _rows(self, values: ArrayLike) -> tuple[NDArray[np.float64], tuple[int, ...]]:
        """The values as one row of cells per time step, and the dimensions before the grid."""
        grid = np.asarray(values, dtype=float)
        if grid.shape[-2:] != self.shape:
            raise ValueError(f"values must end in the grid's shape {self.shape}, not {grid.shape}")
        return grid.reshape(-1, self.shape[0] * self.shape[1]), grid.shape[:-2]

    def _gather(
        self, rows: NDArray[np.float64]
    ) -> Iterator[tuple[slice, NDArray[np.float64], NDArray[np.float64]]]:
        """Each entry's value and people, a few rows at a time.

        Yields the rows covered, each entry's value in them, and its people. Where an
        entry has no value, because its cell has none or it is outside the grid, the
        value is NaN and the people are 0. Working a few rows at a time keeps a long
        series of days to no more than ``_CHUNK`` gathered values in memory at once.
        """
        inside = self.cell != NO_CELL
        # Entries outside the grid read cell 0 here, and are then counted as unknown.
        cells = np.where(inside, self.cell, 0)
        step = max(1, _CHUNK // max(len(self.cell), 1))
        for start in range(0, len(rows), step):
            part = slice(start, start + step)
            picked = rows[part, cells]
            known = inside & np.isfinite(picked)
            yield part, np.where(known, picked, np.nan), np.where(known, self.people, 0.0)

    @staticmethod
    def _line_up(
        picked: NDArray[np.float64], people: NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """One area's values from the lowest to the highest, and the people up to each.

        Entries without a value go last, and add nobody.
        """
        order = np.argsort(np.where(people > 0, picked, np.inf), axis=1, kind="stable")
        ranked = np.take_along_axis(picked, order, axis=1)
        return ranked, np.cumsum(np.take_along_axis(people, order, axis=1), axis=1)

    def _segments(self) -> tuple[NDArray[np.intp], NDArray[np.intp]]:
        """Where each area's entries start, and how many there are: they lie together."""
        counts = np.bincount(self.area, minlength=len(self.codes))
        starts = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.intp)
        return starts, counts.astype(np.intp)

    def _per_area(self, entries: NDArray[np.float64]) -> NDArray[np.float64]:
        """Each row's sum over each area's entries."""
        starts, counts = self._segments()
        totals = np.zeros((len(entries), len(self.codes)))
        filled = counts > 0
        if filled.any():
            totals[:, filled] = np.add.reduceat(entries, starts[filled], axis=1)
        return totals

    def save(self, path: str | PathLike[str]) -> None:
        """Writes the weights to an ``.npz`` file, which :meth:`load` reads back."""
        np.savez(
            path,
            format=np.array(FORMAT_VERSION),
            codes=np.array(self.codes, dtype=str),
            area=self.area,
            cell=self.cell,
            people=self.people,
            lat=self.lat,
            lon=self.lon,
        )

    @classmethod
    def load(cls, path: str | PathLike[str]) -> AreaWeights:
        """Reads weights that :meth:`save` wrote."""
        with np.load(path, allow_pickle=False) as stored:
            if int(stored["format"]) != FORMAT_VERSION:
                raise ValueError(f"weights saved in format {int(stored['format'])}, not 1")
            return cls(
                codes=tuple(str(code) for code in stored["codes"]),
                area=stored["area"].astype(np.intp),
                cell=stored["cell"].astype(np.intp),
                people=stored["people"].astype(float),
                lat=stored["lat"].astype(float),
                lon=stored["lon"].astype(float),
            )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AreaWeights):
            return NotImplemented
        return self.codes == other.codes and all(
            np.array_equal(getattr(self, name), getattr(other, name))
            for name in ("area", "cell", "people", "lat", "lon")
        )


@dataclass(frozen=True, eq=False)
class AreaValues:
    """One value per area, from values on a grid: what :meth:`AreaWeights.apply` returns.

    Both arrays have the shape of the values given, with the grid's two dimensions
    replaced by one for the areas, in the order of :attr:`codes`.
    """

    codes: tuple[str, ...]
    """The areas' codes, in the order of the last dimension."""
    mean: NDArray[np.float64]
    """Each area's population-weighted mean, or NaN where no value covers its people."""
    covered: NDArray[np.float64]
    """The share of each area's people that its mean covers, from 0 to 1.

    It is below 1 where some of the area's people live in cells without a value, or
    outside the grid, and NaN for an area where nobody lives. For example, it is 0.9
    when 900,000 of an area's 1,000,000 people live in cells with a value.
    """


class _Grid:
    """The grid the values come on, given by the centres of its cells.

    The grid is checked once, when it is made. Each axis needs at least two centres, in
    ascending or descending order, and the longitudes must span less than 360 degrees.
    Each cell reaches halfway to its neighbours, and the outer cells reach half a cell
    beyond their centres.
    """

    def __init__(self, lat: ArrayLike, lon: ArrayLike) -> None:
        self.lat = np.asarray(lat, dtype=float)
        """The latitudes of the cell centres, as given."""
        self.lon = np.asarray(lon, dtype=float)
        """The longitudes of the cell centres, as given."""
        self._lat_centres, self._lat_reversed = self._ascending(self.lat, "lat")
        self._lon_centres, self._lon_reversed = self._ascending(self.lon, "lon")
        if self._lon_centres[-1] - self._lon_centres[0] >= 360:
            raise ValueError("lon must span less than 360 degrees")

    def cells(
        self, point_lat: NDArray[np.float64], point_lon: NDArray[np.float64]
    ) -> NDArray[np.intp]:
        """The grid cell of each point, as ``row * len(lon) + column``, or :data:`NO_CELL`."""
        # Bring each point's longitude into the 360 degrees that start at the grid's western
        # edge, so that -10 and 350 find the same cell on any grid.
        west = self._edges(self._lon_centres)[0]
        shifted = west + np.mod(point_lon - west, 360.0)
        row = self._place(point_lat, self._lat_centres, self._lat_reversed)
        column = self._place(shifted, self._lon_centres, self._lon_reversed)
        inside = (row != NO_CELL) & (column != NO_CELL)
        return np.where(inside, row * len(self._lon_centres) + column, NO_CELL).astype(np.intp)

    @staticmethod
    def _ascending(centres: NDArray[np.float64], name: str) -> tuple[NDArray[np.float64], bool]:
        """An axis's centres in ascending order, and whether they were given descending."""
        if centres.ndim != 1 or len(centres) < 2:
            raise ValueError(f"{name} must list at least two cell centres")
        steps = np.diff(centres)
        if np.all(steps > 0):
            return centres, False
        if np.all(steps < 0):
            return centres[::-1], True
        raise ValueError(f"{name} must be in ascending or descending order")

    @staticmethod
    def _edges(centres: NDArray[np.float64]) -> NDArray[np.float64]:
        """The edges of the cells around ascending centres: halfway between neighbours, and
        half a cell beyond the first and the last."""
        middles = (centres[1:] + centres[:-1]) / 2
        first = centres[0] - (centres[1] - centres[0]) / 2
        last = centres[-1] + (centres[-1] - centres[-2]) / 2
        return np.concatenate([[first], middles, [last]])

    @classmethod
    def _place(
        cls, points: NDArray[np.float64], centres: NDArray[np.float64], reverse: bool
    ) -> NDArray[np.intp]:
        """The cell containing each point along one axis, or :data:`NO_CELL`.

        ``centres`` are ascending. When ``reverse`` is true, the axis was given descending,
        so the position is counted from the other end.
        """
        index = np.searchsorted(cls._edges(centres), points, side="right") - 1
        outside = (index < 0) | (index >= len(centres))
        if reverse:
            index = len(centres) - 1 - index
        return np.where(outside, NO_CELL, index).astype(np.intp)

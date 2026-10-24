"""Tests for the population weights.

The weights are checked three ways: on a case small enough to work out by hand,
against an independent implementation written here, and on the edge cases the rules
name. The independent implementation shares no code with the library: it finds the
cell by the nearest centre, one point at a time, and tests whether a point is inside a
polygon by counting how many times a ray from it crosses the polygon's edges.
"""

from __future__ import annotations

from itertools import accumulate
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from terragrids.areas import NO_CELL, AreaWeights

Ring = list[tuple[float, float]]

# A grid of 2 × 2 cells, 1° across, centred on 0° and 1°: its edges are at -0.5°, 0.5°
# and 1.5°, in both directions.
LAT = np.array([0.0, 1.0])
LON = np.array([0.0, 1.0])


def pairs(weights: AreaWeights) -> dict[tuple[str, int], float]:
    """The weights as {(area code, cell): people}."""
    return {
        (weights.codes[a], int(c)): float(p)
        for a, c, p in zip(weights.area, weights.cell, weights.people, strict=True)
    }


# A case worked out by hand


def test_people_are_split_by_area_and_cell() -> None:
    # South: the lower row of cells. A city of 900 in cell 0, a village of 100 in cell 1.
    # North: the upper row, with 50 people in cell 3. 10 people live in neither area.
    areas = {"south": box(-0.5, -0.5, 1.5, 0.5), "north": box(-0.5, 0.5, 1.5, 1.5)}
    weights = AreaWeights.from_population(
        areas,
        population_lat=[0.0, 0.2, 1.1, 5.0],
        population_lon=[0.0, 1.1, 1.0, 5.0],
        population=[900, 100, 50, 10],
        lat=LAT,
        lon=LON,
    )
    assert weights.codes == ("south", "north")
    assert pairs(weights) == {("south", 0): 900, ("south", 1): 100, ("north", 3): 50}
    assert weights.population.tolist() == [1000, 50]
    assert weights.shape == (2, 2)


# The independent implementation


def ray_crossings(lat: float, lon: float, ring: Ring) -> int:
    """How many edges of a closed ring a ray going east from the point crosses."""
    crossings = 0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            crossings += 1
    return crossings


def inside(lat: float, lon: float, parts: list[tuple[Ring, list[Ring]]]) -> bool:
    """Whether a point is inside any part: inside its outline and outside its holes."""
    for outline, holes in parts:
        if ray_crossings(lat, lon, outline) % 2 == 1:
            if not any(ray_crossings(lat, lon, hole) % 2 == 1 for hole in holes):
                return True
    return False


def nearest(value: float, centres: list[float]) -> int:
    """The nearest centre's position, or NO_CELL if the value is beyond the outer edges."""
    step = centres[1] - centres[0]
    if not centres[0] - step / 2 <= value < centres[-1] + step / 2:
        return NO_CELL
    return min(range(len(centres)), key=lambda i: abs(centres[i] - value))


def reference(
    areas: dict[str, list[tuple[Ring, list[Ring]]]],
    points: list[tuple[float, float, float]],
    lat: list[float],
    lon: list[float],
) -> dict[tuple[str, int], float]:
    """The weights, worked out one point and one area at a time."""
    totals: dict[tuple[str, int], float] = {}
    for point_lat, point_lon, people in points:
        if not people > 0:
            continue
        row, column = nearest(point_lat, lat), nearest(point_lon, lon)
        cell = NO_CELL if NO_CELL in (row, column) else row * len(lon) + column
        for code, parts in areas.items():
            if inside(point_lat, point_lon, parts):
                totals[code, cell] = totals.get((code, cell), 0.0) + people
    return totals


def star(lat: float, lon: float, radius: float, rng: np.random.Generator) -> Ring:
    """A random star-shaped outline around a centre."""
    angles = np.sort(rng.uniform(0, 2 * np.pi, 9))
    radii = radius * rng.uniform(0.5, 1.0, 9)
    return [(lon + r * np.cos(a), lat + r * np.sin(a)) for a, r in zip(angles, radii, strict=True)]


def random_case() -> tuple[AreaWeights, dict[tuple[str, int], float], list[float], list[float]]:
    """Weights for generated areas and points, the reference's pairs, and the grid."""
    rng = np.random.default_rng(20261021)
    lat = [float(v) for v in np.arange(40.0, 46.0, 0.25)]
    lon = [float(v) for v in np.arange(-3.0, 4.0, 0.25)]
    hole = [(0.5, 43.0), (1.0, 43.0), (1.0, 43.5), (0.5, 43.5)]
    areas: dict[str, list[tuple[Ring, list[Ring]]]] = {
        "plain": [(star(41.0, -1.0, 1.2, rng), [])],
        "with a hole": [([(0.0, 42.5), (1.5, 42.5), (1.5, 44.0), (0.0, 44.0)], [hole])],
        "two parts": [(star(44.5, -2.0, 0.6, rng), []), (star(41.5, 2.5, 0.8, rng), [])],
        # It reaches past the grid's northern edge, at 46.125°.
        "past the edge": [(star(45.8, 0.0, 0.9, rng), [])],
    }
    count = 4000
    points_lat = rng.uniform(39.0, 47.0, count)
    points_lon = rng.uniform(-4.0, 5.0, count)
    people = rng.integers(0, 500, count).astype(float)
    people[::97] = np.nan

    shapes = {
        code: MultiPolygon([Polygon(outline, holes) for outline, holes in parts])
        for code, parts in areas.items()
    }
    weights = AreaWeights.from_population(shapes, points_lat, points_lon, people, lat, lon)
    expected = reference(areas, list(zip(points_lat, points_lon, people, strict=True)), lat, lon)
    return weights, expected, lat, lon


def test_the_weights_match_an_independent_implementation() -> None:
    weights, expected, _, _ = random_case()
    got = pairs(weights)
    assert got.keys() == expected.keys()
    assert all(got[key] == pytest.approx(expected[key]) for key in expected)
    assert any(cell == NO_CELL for _, cell in got)


# The grid conventions


def test_descending_latitudes_and_longitudes_from_0_to_360_find_the_same_cells() -> None:
    # A global grid of 1° cells, as ERA5 files lay it out: latitude from north to south,
    # longitude from 0 to 359.
    lat = np.arange(10.0, -11.0, -1.0)
    lon = np.arange(0.0, 360.0, 1.0)
    area = {"around 0°": box(-3.0, -3.0, 3.0, 3.0)}
    weights = AreaWeights.from_population(
        area,
        population_lat=[2.2, -1.9],
        population_lon=[-1.1, 2.0],
        population=[7, 3],
        lat=lat,
        lon=lon,
    )
    # 2.2°N is in the row centred on 2°, the 9th from the north. -1.1° is 358.9°: the
    # column centred on 359°.
    assert pairs(weights) == {
        ("around 0°", 8 * 360 + 359): 7,
        ("around 0°", 12 * 360 + 2): 3,
    }


# The rules for edge cases


def test_people_outside_the_grid_stay_in_their_area_with_no_cell() -> None:
    weights = AreaWeights.from_population(
        {"wide": box(-0.5, -0.5, 4.0, 1.5)},
        population_lat=[0.0, 1.0],
        population_lon=[0.0, 3.0],
        population=[60, 40],
        lat=LAT,
        lon=LON,
    )
    assert pairs(weights) == {("wide", NO_CELL): 40, ("wide", 0): 60}
    assert weights.population.tolist() == [100]


def test_overlapping_areas_and_shared_borders_count_a_point_in_both() -> None:
    areas = {"west": box(-0.5, -0.5, 0.5, 1.5), "east": box(0.5, -0.5, 1.5, 1.5)}
    weights = AreaWeights.from_population(
        areas,
        population_lat=[0.0, 1.0],
        population_lon=[0.5, 1.2],
        population=[20, 5],
        lat=LAT,
        lon=LON,
    )
    # The point at 0.5° lies on the border: in both areas, and in cell 1 (its edge).
    assert pairs(weights) == {("west", 1): 20, ("east", 1): 20, ("east", 3): 5}


def test_points_with_no_people_are_skipped() -> None:
    weights = AreaWeights.from_population(
        {"all": box(-0.5, -0.5, 1.5, 1.5)},
        population_lat=[0.0, 0.0, 1.0, 1.0],
        population_lon=[0.0, 1.0, 0.0, 1.0],
        population=[0, -200, np.nan, 8],
        lat=LAT,
        lon=LON,
    )
    assert pairs(weights) == {("all", 3): 8}


def test_an_area_where_nobody_lives_has_no_entries() -> None:
    weights = AreaWeights.from_population(
        {"empty": box(10, 10, 11, 11)},
        population_lat=[0.0],
        population_lon=[0.0],
        population=[5],
        lat=LAT,
        lon=LON,
    )
    assert pairs(weights) == {}
    assert weights.population.tolist() == [0]


@pytest.mark.parametrize(
    ("lat", "lon", "message"),
    [
        ([0.0], LON, "at least two"),
        (LAT, [0.0, 2.0, 1.0], "ascending or descending"),
        (LAT, np.arange(0.0, 361.0, 1.0), "less than 360"),
    ],
)
def test_a_grid_it_cannot_read_is_refused(lat: object, lon: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AreaWeights.from_population({}, [0.0], [0.0], [1.0], lat, lon)  # type: ignore[arg-type]


def test_points_must_give_a_count_for_every_position() -> None:
    with pytest.raises(ValueError, match="differ in length"):
        AreaWeights.from_population({}, [0.0, 1.0], [0.0], [1.0], LAT, LON)


# Saving and loading


def test_weights_saved_and_loaded_are_identical(tmp_path: Path) -> None:
    areas = {"south": box(-0.5, -0.5, 1.5, 0.5), "north": box(-0.5, 0.5, 4.0, 1.5)}
    weights = AreaWeights.from_population(
        areas, [0.0, 0.2, 1.1, 1.0], [0.0, 1.1, 1.0, 3.0], [900, 100, 50, 7], LAT, LON
    )
    path = tmp_path / "weights.npz"
    weights.save(path)
    loaded = AreaWeights.load(path)
    assert loaded == weights
    assert loaded.codes == ("south", "north")


def test_weights_in_an_unknown_format_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "weights.npz"
    np.savez(path, format=np.array(2))
    with pytest.raises(ValueError, match="format 2"):
        AreaWeights.load(path)


def test_weights_whose_entries_are_out_of_order_are_refused() -> None:
    grid = {"lat": LAT, "lon": LON}
    with pytest.raises(ValueError, match="sorted by area"):
        AreaWeights(("a", "b"), np.array([1, 0]), np.array([0, 1]), np.ones(2), **grid)
    with pytest.raises(ValueError, match="differ in length"):
        AreaWeights(("a",), np.array([0]), np.array([0, 1]), np.ones(1), **grid)


# Applying the weights

# An area over the lower row of cells: 900,000 people in the city cell, cell 0, and
# 100,000 in the rural cell, cell 1. The values are given by row: latitude 0°, then 1°.
REGION = AreaWeights.from_population(
    {"region": box(-0.5, -0.5, 1.5, 0.5)},
    population_lat=[0.0, 0.0],
    population_lon=[0.0, 1.0],
    population=[900_000, 100_000],
    lat=LAT,
    lon=LON,
)


def test_the_mean_weights_each_cell_by_its_people() -> None:
    result = REGION.apply([[36.0, 30.0], [20.0, 20.0]])
    # (900,000 × 36 + 100,000 × 30) / 1,000,000, where the plain average is 33.
    assert result.codes == ("region",)
    assert result.mean.tolist() == pytest.approx([35.4])
    assert result.covered.tolist() == [1.0]


def test_a_cell_without_a_value_is_left_out_and_lowers_the_share() -> None:
    result = REGION.apply([[36.0, np.nan], [20.0, 20.0]])
    assert result.mean.tolist() == [36.0]
    assert result.covered.tolist() == pytest.approx([0.9])


def test_people_outside_the_grid_lower_the_share_without_changing_the_mean() -> None:
    # The region of the module's example, reaching past the grid's eastern edge, at
    # 1.5°, with 50,000 more people at 3°E.
    weights = AreaWeights.from_population(
        {"region": box(-0.5, -0.5, 4.0, 0.5)},
        population_lat=[0.0, 0.0, 0.0],
        population_lon=[0.0, 1.0, 3.0],
        population=[900_000, 100_000, 50_000],
        lat=LAT,
        lon=LON,
    )
    result = weights.apply([[36.0, 30.0], [20.0, 20.0]])
    assert result.mean.tolist() == pytest.approx([35.4])
    assert result.covered.tolist() == pytest.approx([1_000_000 / 1_050_000])
    assert round(float(result.covered[0]), 2) == 0.95


def test_each_day_is_averaged_on_its_own() -> None:
    days = [
        [[36.0, 30.0], [20.0, 20.0]],
        [[30.0, 36.0], [20.0, 20.0]],
        [[np.nan, 30.0], [20.0, 20.0]],
    ]
    result = REGION.apply(days)
    assert result.mean.shape == (3, 1)
    assert result.mean[:, 0].tolist() == pytest.approx([35.4, 30.6, 30.0])
    assert result.covered[:, 0].tolist() == pytest.approx([1.0, 1.0, 0.1])


def test_people_outside_the_grid_are_left_out_and_lower_the_share() -> None:
    weights = AreaWeights.from_population(
        {"wide": box(-0.5, -0.5, 4.0, 1.5)},
        population_lat=[0.0, 1.0],
        population_lon=[0.0, 3.0],
        population=[60, 40],
        lat=LAT,
        lon=LON,
    )
    result = weights.apply([[25.0, 30.0], [30.0, 30.0]])
    assert result.mean.tolist() == [25.0]
    assert result.covered.tolist() == pytest.approx([0.6])


def test_an_area_where_nobody_lives_gets_no_value() -> None:
    areas = {
        "south": box(-0.5, -0.5, 1.5, 0.5),
        "empty": box(10, 10, 11, 11),
        "north": box(-0.5, 0.5, 1.5, 1.5),
    }
    weights = AreaWeights.from_population(areas, [0.0, 1.0], [0.0, 1.0], [10, 30], LAT, LON)
    result = weights.apply([[20.0, 21.0], [22.0, 23.0]])
    assert result.mean[[0, 2]].tolist() == [20.0, 23.0]
    assert result.covered[[0, 2]].tolist() == [1.0, 1.0]
    assert np.isnan(result.mean[1]) and np.isnan(result.covered[1])


def test_an_area_with_no_value_anywhere_gets_no_value() -> None:
    result = REGION.apply([[np.nan, np.nan], [20.0, 20.0]])
    assert np.isnan(result.mean[0])
    assert result.covered.tolist() == [0.0]


@pytest.mark.parametrize("values", [np.zeros((3, 3)), np.zeros(4), np.zeros((2, 2, 1))])
def test_values_on_another_grid_are_refused(values: np.ndarray) -> None:
    with pytest.raises(ValueError, match="grid's shape"):
        REGION.apply(values)


def test_saved_and_loaded_weights_give_the_same_values(tmp_path: Path) -> None:
    weights, _, lat, lon = random_case()
    values = np.random.default_rng(1).uniform(10.0, 40.0, (2, len(lat), len(lon)))
    path = tmp_path / "weights.npz"
    weights.save(path)
    before, after = weights.apply(values), AreaWeights.load(path).apply(values)
    assert np.array_equal(before.mean, after.mean, equal_nan=True)
    assert np.array_equal(before.covered, after.covered, equal_nan=True)


def test_the_means_match_an_independent_implementation() -> None:
    weights, expected, lat, lon = random_case()
    rng = np.random.default_rng(2)
    values = rng.uniform(10.0, 40.0, (2, len(lat), len(lon)))
    values[rng.uniform(size=values.shape) < 0.1] = np.nan
    result = weights.apply(values)

    for day in range(2):
        flat = values[day].ravel()
        for position, code in enumerate(weights.codes):
            entries = [(cell, people) for (c, cell), people in expected.items() if c == code]
            total = sum(people for _, people in entries)
            known = [(flat[cell], people) for cell, people in entries if cell != NO_CELL]
            known = [(value, people) for value, people in known if not np.isnan(value)]
            counted = sum(people for _, people in known)
            mean = sum(value * people for value, people in known) / counted
            assert result.mean[day, position] == pytest.approx(mean)
            assert result.covered[day, position] == pytest.approx(counted / total)
    assert np.all(result.covered < 1.0)


def test_values_gathered_in_parts_give_the_same_means(monkeypatch: pytest.MonkeyPatch) -> None:
    weights, _, lat, lon = random_case()
    values = np.random.default_rng(3).uniform(10.0, 40.0, (7, len(lat), len(lon)))
    whole = weights.apply(values)
    # Small enough that the seven days are gathered two at a time.
    monkeypatch.setattr("terragrids.areas._CHUNK", 2 * len(weights.cell))
    parts = weights.apply(values)
    assert np.array_equal(whole.mean, parts.mean, equal_nan=True)
    assert np.array_equal(whole.covered, parts.covered, equal_nan=True)


# The spread and the share above a threshold

# The four-cell area of the module's example: hills, coast, suburbs and city.
FOUR_CELLS = AreaWeights.from_population(
    {"area": box(-0.5, -0.5, 1.5, 1.5)},
    population_lat=[0.0, 0.0, 1.0, 1.0],
    population_lon=[0.0, 1.0, 0.0, 1.0],
    population=[50_000, 150_000, 300_000, 500_000],
    lat=LAT,
    lon=LON,
)
FOUR_VALUES = [[25.0, 28.0], [34.0, 37.0]]


def test_the_module_example_gives_the_values_it_states() -> None:
    assert FOUR_CELLS.apply(FOUR_VALUES).mean.tolist() == pytest.approx([34.15])
    assert FOUR_CELLS.percentiles(FOUR_VALUES).tolist() == [[28.0, 34.0, 37.0]]
    assert FOUR_CELLS.share_above(FOUR_VALUES, 32.0).tolist() == pytest.approx([0.8])


def test_a_percentile_is_the_value_of_the_cell_where_that_person_lives() -> None:
    # The first 50,000 people are in the hills: up to the 5th percentile, it is 25 °C.
    q = [0.0, 5.0, 5.1, 20.0, 20.1, 100.0]
    assert FOUR_CELLS.percentiles(FOUR_VALUES, q).tolist() == [[25, 25, 28, 28, 34, 37]]


def test_the_two_cell_region_has_its_city_at_the_90th_percentile() -> None:
    assert REGION.percentiles([[36.0, 30.0], [20.0, 20.0]]).tolist() == [[30.0, 36.0, 36.0]]
    assert REGION.share_above([[36.0, 30.0], [20.0, 20.0]], 33.0).tolist() == pytest.approx([0.9])


def test_a_value_equal_to_the_threshold_is_not_above_it() -> None:
    assert FOUR_CELLS.share_above(FOUR_VALUES, 34.0).tolist() == pytest.approx([0.5])
    assert FOUR_CELLS.share_above(FOUR_VALUES, 37.0).tolist() == [0.0]
    assert FOUR_CELLS.share_above(FOUR_VALUES, 24.0).tolist() == [1.0]


def test_the_spread_counts_only_the_people_with_a_value() -> None:
    # Without the city's value, 500,000 people remain: 300,000 of them in the suburbs.
    # 10% of them is 50,000 people, so the 10th percentile falls on the last person in
    # the hills, exactly.
    values = [[25.0, 28.0], [34.0, np.nan]]
    assert FOUR_CELLS.percentiles(values).tolist() == [[25.0, 34.0, 34.0]]
    assert FOUR_CELLS.share_above(values, 32.0).tolist() == pytest.approx([0.6])


def test_the_spread_keeps_the_days_and_adds_a_dimension_for_q() -> None:
    days = np.stack([FOUR_VALUES, np.add(FOUR_VALUES, 1.0), np.add(FOUR_VALUES, 2.0)])
    spread = FOUR_CELLS.percentiles(days)
    assert spread.shape == (3, 1, 3)
    assert spread[:, 0, 2].tolist() == [37.0, 38.0, 39.0]
    assert FOUR_CELLS.share_above(days, 35.5)[:, 0].tolist() == pytest.approx([0.5, 0.5, 0.8])


def test_an_area_without_covered_people_has_no_spread() -> None:
    areas = {"south": box(-0.5, -0.5, 1.5, 0.5), "empty": box(10, 10, 11, 11)}
    weights = AreaWeights.from_population(areas, [0.0], [0.0], [10], LAT, LON)
    values = [[20.0, 21.0], [22.0, 23.0]]
    assert np.isnan(weights.percentiles(values)[1]).all()
    assert np.isnan(weights.share_above(values, 0.0)[1])
    unknown = [[np.nan, 21.0], [22.0, 23.0]]
    assert np.isnan(weights.percentiles(unknown)).all()
    assert np.isnan(weights.share_above(unknown, 0.0)).all()


@pytest.mark.parametrize("q", [[-1.0], [101.0], [[10.0, 90.0]]])
def test_percentages_it_cannot_read_are_refused(q: list[float]) -> None:
    with pytest.raises(ValueError, match="percentages from 0 to 100"):
        FOUR_CELLS.percentiles(FOUR_VALUES, q)


def test_the_spread_matches_an_independent_implementation(monkeypatch: pytest.MonkeyPatch) -> None:
    weights, expected, lat, lon = random_case()
    rng = np.random.default_rng(4)
    values = rng.uniform(10.0, 40.0, (3, len(lat), len(lon)))
    values[rng.uniform(size=values.shape) < 0.1] = np.nan
    q = [0.0, 10.0, 25.0, 50.0, 90.0, 100.0]
    spread = weights.percentiles(values, q)
    above = weights.share_above(values, 25.0)

    for day in range(3):
        flat = values[day].ravel()
        for position, code in enumerate(weights.codes):
            known = sorted(
                (flat[cell], people)
                for (c, cell), people in expected.items()
                if c == code and cell != NO_CELL and not np.isnan(flat[cell])
            )
            total = sum(people for _, people in known)
            running = list(accumulate(people for _, people in known))
            for k, share in enumerate(q):
                # People are whole numbers, so this comparison is exact.
                chosen = next(
                    value
                    for (value, _), so_far in zip(known, running, strict=True)
                    if so_far * 100 >= share * total
                )
                assert spread[day, position, k] == chosen
            hot = sum(people for value, people in known if value > 25.0)
            assert above[day, position] == pytest.approx(hot / total)

    # The same results when the days are gathered one at a time.
    monkeypatch.setattr("terragrids.areas._CHUNK", len(weights.cell))
    assert np.array_equal(weights.percentiles(values, q), spread)
    assert np.array_equal(weights.share_above(values, 25.0), above)

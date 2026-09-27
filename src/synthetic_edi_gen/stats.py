"""Typed, cached access to the packaged aggregate statistics."""

# ruff: noqa: S311
from __future__ import annotations

import random
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from datetime import date
from functools import cache, lru_cache
from importlib.resources import files
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict


class StatsBaseModel(BaseModel):
    """Base for the stats interchange schema."""

    __slots__ = ()
    model_config = ConfigDict(extra="forbid")


class StatsGeneration(StatsBaseModel):
    source_database_count: int
    contains_organization_breakdown: bool
    contains_direct_identifiers: bool
    contains_exact_dates: bool
    correlation_row_limit: int


class StatsCounts(StatsBaseModel):
    claims: int
    claim_service_lines: int
    payments: int
    ar_records: int
    cases: int
    unique_patients: int
    unique_providers: int


class StatsRatios(StatsBaseModel):
    unique_patients_per_claim: float
    unique_providers_per_claim: float
    service_lines_per_claim: float
    payments_per_claim: float


class CorrelationCoverage(StatsBaseModel):
    dimensions: list[str]
    total_observations: int
    retained_observations: int
    conditioning_totals: dict[str, int]
    omitted_by_condition: dict[str, int]


class StatsSemantics(StatsBaseModel):
    amount_buckets: str
    percent_buckets: str
    length_of_stay_days: str
    correlation_coverage: str
    facility_code: dict[str, str]


class Stats(StatsBaseModel):
    schema_version: Literal[2]
    generation: StatsGeneration
    counts: StatsCounts
    ratios: StatsRatios
    distributions: dict[str, dict[str, int]]
    correlations: dict[str, dict[str, int]]
    correlation_coverage: dict[str, CorrelationCoverage]
    catalogs: dict[str, dict[str, str]]
    semantics: StatsSemantics


class _Random(Protocol):
    def random(self) -> float: ...

    def randint(self, a: int, b: int) -> int: ...


@cache
def load_stats() -> Stats:
    """Load the packaged JSON once per process."""
    resource = files("synthetic_edi_gen").joinpath("data/stats.json")
    return Stats.model_validate_json(resource.read_bytes())


def distribution(name: str) -> Mapping[str, int]:
    return load_stats().distributions[name]


def catalog(name: str) -> Mapping[str, str]:
    return load_stats().catalogs[name]


def ratio(name: str) -> float:
    return float(getattr(load_stats().ratios, name))


@cache
def _conditional_index(name: str) -> dict[str, dict[str, int]]:
    rows = load_stats().correlations[name]
    index: dict[str, dict[str, int]] = {}
    for pair, count in rows.items():
        left, right = pair.rsplit("|", 1)
        index.setdefault(left, {})[right] = count
    return index


def _condition_key(value: str | Sequence[str]) -> str:
    return value if isinstance(value, str) else "|".join(value)


def conditional(name: str, value: str | Sequence[str]) -> Mapping[str, int]:
    return _conditional_index(name).get(_condition_key(value), {})


@cache
def _reverse_conditional_index(name: str) -> dict[str, dict[str, int]]:
    rows = load_stats().correlations[name]
    index: dict[str, dict[str, int]] = {}
    for pair, count in rows.items():
        left, right = pair.rsplit("|", 1)
        index.setdefault(right, {})[left] = count
    return index


@lru_cache(maxsize=512)
def _reverse_conditional_table(
    correlation: str,
    value: str,
    fallback: str,
    allowed: tuple[str, ...] | None,
) -> tuple[tuple[str, ...], tuple[float, ...], float]:
    stats = load_stats()
    outcome_name = next(
        (name for name in stats.distributions if correlation.endswith(f"_{name}")),
        None,
    )
    if outcome_name is None:
        weights: Mapping[str, int | float] = _reverse_conditional_index(
            correlation
        ).get(value, {})
    else:
        outcome_base = distribution(outcome_name)
        coverage = stats.correlation_coverage[correlation]
        weights = {}
        for condition, base_count in distribution(fallback).items():
            retained = conditional(correlation, condition)
            conditioning_total = coverage.conditioning_totals.get(condition, 0)
            if not conditioning_total:
                continue
            pair_count = retained.get(value)
            if pair_count is None:
                omitted = coverage.omitted_by_condition.get(condition, 0)
                unretained_total = sum(
                    count
                    for outcome, count in outcome_base.items()
                    if outcome not in retained
                )
                pair_count = (
                    omitted * outcome_base.get(value, 0) / unretained_total
                    if omitted and unretained_total
                    else 0
                )
            weights[condition] = base_count * pair_count / conditioning_total
    if allowed is not None:
        allowed_set = set(allowed)
        weights = {key: count for key, count in weights.items() if key in allowed_set}
    return _weighted_table(weights)


@cache
def _conditional_parts(
    correlation: str,
    value: str,
    fallback: str,
) -> tuple[Mapping[str, int], float, float]:
    base = distribution(fallback)
    retained = conditional(correlation, value)
    if not retained:
        return base, 0.0, float(sum(base.values()))

    coverage = load_stats().correlation_coverage[correlation]
    omitted = coverage.omitted_by_condition.get(value, 0)
    unretained_total = sum(count for key, count in base.items() if key not in retained)
    omitted_scale = omitted / unretained_total if omitted and unretained_total else 0.0
    total = float(sum(retained.values()) + (omitted if omitted_scale else 0))
    return retained, omitted_scale, total


def _conditional_weights(
    correlation: str,
    value: str | Sequence[str],
    fallback: str,
) -> dict[str, float]:
    """Restore capped correlation mass using its measured omitted count."""
    base = distribution(fallback)
    retained, omitted_scale, _ = _conditional_parts(
        correlation, _condition_key(value), fallback
    )
    weights = {key: float(count) for key, count in retained.items()}
    if omitted_scale:
        weights.update(
            {
                key: omitted_scale * count
                for key, count in base.items()
                if key not in retained
            }
        )
    return weights


def _weighted_table(
    weights: Mapping[str, int | float],
) -> tuple[tuple[str, ...], tuple[float, ...], float]:
    values: list[str] = []
    cumulative: list[float] = []
    total = 0.0
    for value, weight in weights.items():
        if weight <= 0:
            continue
        values.append(value)
        total += weight
        cumulative.append(total)
    return tuple(values), tuple(cumulative), total


def _pick(table: tuple[tuple[str, ...], tuple[float, ...], float], rng: _Random) -> str:
    values, cumulative, total = table
    return values[bisect_right(cumulative, rng.random() * total, hi=len(values) - 1)]


@cache
def _distribution_table(
    name: str, allowed: tuple[str, ...] | None
) -> tuple[tuple[str, ...], tuple[float, ...], float]:
    weights = distribution(name)
    if allowed is not None:
        allowed_set = set(allowed)
        weights = {key: value for key, value in weights.items() if key in allowed_set}
    return _weighted_table(weights)


def sample_distribution(
    name: str,
    *,
    allowed: set[str] | None = None,
    rng: _Random = random,
) -> str:
    table = _distribution_table(name, tuple(sorted(allowed)) if allowed else None)
    if not table[0]:
        raise ValueError(f"no values available for distribution {name!r}")
    return _pick(table, rng)


@lru_cache(maxsize=512)
def _conditional_table(
    correlation: str,
    value: str,
    fallback: str,
    allowed: tuple[str, ...] | None,
) -> tuple[tuple[str, ...], tuple[float, ...], float]:
    weights = _conditional_weights(correlation, value, fallback)
    if allowed is not None:
        allowed_set = set(allowed)
        weights = {key: count for key, count in weights.items() if key in allowed_set}
    return _weighted_table(weights)


def sample_conditional(
    correlation: str,
    value: str | Sequence[str],
    *,
    fallback: str,
    allowed: set[str] | None = None,
    rng: _Random = random,
) -> str:
    table = _conditional_table(
        correlation,
        _condition_key(value),
        fallback,
        tuple(sorted(allowed)) if allowed else None,
    )
    if not table[0]:
        return sample_distribution(fallback, allowed=allowed, rng=rng)
    return _pick(table, rng)


def sample_reverse_conditional(
    correlation: str,
    value: str,
    *,
    fallback: str,
    allowed: set[str] | None = None,
    rng: _Random = random,
) -> str:
    table = _reverse_conditional_table(
        correlation,
        value,
        fallback,
        tuple(sorted(allowed)) if allowed else None,
    )
    if not table[0]:
        return sample_distribution(fallback, allowed=allowed, rng=rng)
    return _pick(table, rng)


@lru_cache(maxsize=256)
def _correlated_table(
    distribution_name: str,
    conditions: tuple[tuple[str, str], ...],
    allowed: tuple[str, ...] | None,
) -> tuple[tuple[str, ...], tuple[float, ...], float]:
    base = distribution(distribution_name)
    total = sum(base.values())
    conditionals = [
        _conditional_parts(name, value, distribution_name) for name, value in conditions
    ]
    allowed_set = set(allowed) if allowed is not None else None
    weights: dict[str, float] = {}
    for key, base_count in base.items():
        if allowed_set is not None and key not in allowed_set:
            continue
        weight = base_count / total
        for retained, omitted_scale, conditioned_total in conditionals:
            base_probability = base_count / total
            conditional_count = retained.get(key, omitted_scale * base_count)
            conditional_probability = conditional_count / conditioned_total
            weight *= conditional_probability / base_probability
        weights[key] = weight
    return _weighted_table(weights)


def sample_correlated(
    distribution_name: str,
    conditions: Sequence[tuple[str, str | Sequence[str]]],
    *,
    allowed: set[str] | None = None,
    rng: _Random = random,
) -> str:
    """Combine conditionals as likelihoods, including capped-table omitted mass."""
    table = _correlated_table(
        distribution_name,
        tuple((name, _condition_key(value)) for name, value in conditions),
        tuple(sorted(allowed)) if allowed else None,
    )
    if not table[0]:
        return sample_distribution(distribution_name, allowed=allowed, rng=rng)
    return _pick(table, rng)


def code_description(catalog_name: str, code: str) -> str:
    return catalog(catalog_name).get(code, code)


def sample_count(
    distribution_name: str,
    *,
    correlation: tuple[str, str | Sequence[str]] | None = None,
    minimum: int = 0,
    rng: _Random = random,
) -> int:
    value = (
        sample_conditional(
            correlation[0], correlation[1], fallback=distribution_name, rng=rng
        )
        if correlation
        else sample_distribution(distribution_name, rng=rng)
    )
    if "-" in value:
        low, high = (int(part) for part in value.split("-", 1))
        count = rng.randint(low, high)
    else:
        count = int(value.rstrip("+"))
    return max(minimum, count)


def sample_presence(distribution_name: str, *, rng: _Random = random) -> bool:
    value = sample_distribution(distribution_name, rng=rng).lower()
    return value in {"1", "present", "true", "yes"}


def _bucket_bounds(bucket: str) -> tuple[float, float]:
    negative = bucket.startswith("negative:")
    bucket = bucket.removeprefix("negative:")
    if bucket.endswith("+"):
        low = float(bucket[:-1])
        high = low * 2
    else:
        low_text, high_text = bucket.split("-", 1)
        low, high = float(low_text), float(high_text)
    if negative:
        return -high, -low
    return low, high


def sample_numeric(
    distribution_name: str,
    *,
    conditions: Sequence[tuple[str, str | Sequence[str]]] = (),
    rng: Any = random,
    precision: int = 2,
) -> float:
    bucket = sample_correlated(
        distribution_name,
        conditions,
        rng=rng,
    )
    low, high = _bucket_bounds(bucket)
    return round(rng.uniform(low, high), precision)


def age_band_for(birth_date: date, on_date: date) -> str:
    age = (
        on_date.year
        - birth_date.year
        - ((on_date.month, on_date.day) < (birth_date.month, birth_date.day))
    )
    if age <= 1:
        return "0-1"
    if age <= 5:
        return "2-5"
    if age <= 12:
        return "6-12"
    if age <= 17:
        return "13-17"
    if age <= 25:
        return "18-25"
    if age <= 34:
        return "26-34"
    if age <= 44:
        return "35-44"
    if age <= 54:
        return "45-54"
    if age <= 64:
        return "55-64"
    if age <= 74:
        return "65-74"
    if age <= 84:
        return "75-84"
    return "85+"


def age_range(age_band: str) -> tuple[int, int]:
    if age_band.endswith("+"):
        return int(age_band[:-1]), 100
    low, high = age_band.split("-", 1)
    return int(low), int(high)

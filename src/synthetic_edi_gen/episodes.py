"""Shared encounter/account sampling, separate from patient identity."""

# ruff: noqa: S311
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from .claim_generator import ClaimGenerator, PatientContext
from .edi_models import InstClaim, ProfClaim
from .stats import load_stats, sample_correlated, sample_count, sample_distribution

Claim = ProfClaim | InstClaim
ClaimType = Literal["PROF", "INST"]


def parse_claim(data: dict[str, Any]) -> Claim:
    model = (
        InstClaim
        if data.get("transaction", {}).get("transactionType") == "INST"
        else ProfClaim
    )
    return model.model_validate(data)


def episode_family() -> str | None:
    """Never mix encounter marginals with account-level conditionals."""
    tables = load_stats().distributions
    for family, patients, claims in (
        ("encounter", "encounters_per_patient", "claims_per_encounter"),
        (
            "billing_episode",
            "billing_episodes_per_patient",
            "claims_per_billing_episode",
        ),
    ):
        if tables.get(patients) and tables.get(claims):
            return family
    return None


def patient_episode_count(family: str) -> int:
    name = (
        "encounters_per_patient"
        if family == "encounter"
        else "billing_episodes_per_patient"
    )
    return sample_count(name, minimum=1)


def episode_days(
    family: str, suffix: str, claim_type: str | None = None, *, minimum: int = 0
) -> int:
    name = f"{family}_{suffix}_days"
    stats = load_stats()
    if not stats.distributions.get(name):
        return minimum
    correlation = f"{family}_claim_type_{suffix}_days"
    return sample_count(
        name,
        correlation=(correlation, claim_type)
        if claim_type and correlation in stats.correlations
        else None,
        minimum=minimum,
        upper_exclusive=suffix in {"claim_service_offset", "claim_submission_offset"},
    )


def _sample(
    name: str, conditions: list[tuple[str, str]], allowed: set[str] | None = None
) -> str:
    stats = load_stats()
    return sample_correlated(
        name,
        [(key, value) for key, value in conditions if key in stats.correlations],
        allowed=allowed,
    )


@dataclass(frozen=True)
class EpisodePlan:
    family: str | None
    setting: str
    claim_types: tuple[ClaimType, ...]
    primary_procedure: str | None = None


def plan_episode(
    ctx: PatientContext,
    *,
    claim_limit: int | None = None,
    institutional_rate: float | None = None,
    legacy_count: int = 1,
) -> EpisodePlan:
    family = episode_family()
    taxonomy = ctx.rendering_provider.provider_taxonomy
    taxonomy_code = taxonomy.code if taxonomy else ""
    if family is None:
        count = (
            min(legacy_count, claim_limit) if claim_limit is not None else legacy_count
        )
        types: tuple[ClaimType, ...] = tuple(
            "INST"
            if (
                random.random() < institutional_rate
                if institutional_rate is not None
                else sample_distribution("claim_type") == "INST"
            )
            else "PROF"
            for _ in range(count)
        )
        return EpisodePlan(None, "unknown", types)

    stats = load_stats()
    setting_name = f"{family}_setting"
    setting = (
        sample_distribution(setting_name)
        if stats.distributions.get(setting_name)
        else "unknown"
    )
    conditions = [
        (f"{family}_setting_claim_type", setting),
        (f"{family}_provider_taxonomy_claim_type", taxonomy_code),
    ]
    mix_name = f"{family}_claim_type_mix"
    supported_mixes = {
        key
        for key in stats.distributions.get(mix_name, {})
        if set(key.split("+")) <= {"INST", "PROF"}
    }
    if institutional_rate is None and supported_mixes:
        mix = _sample(
            mix_name, [(f"{family}_setting_claim_type_mix", setting)], supported_mixes
        )
        required = [
            cast_type for cast_type in ("INST", "PROF") if cast_type in mix.split("+")
        ]
    else:
        required = []
    first_type = (
        ("INST" if random.random() < institutional_rate else "PROF")
        if institutional_rate is not None
        else _sample("claim_type", conditions, set(required) or {"INST", "PROF"})
    )
    if required:
        required = [first_type, *(kind for kind in required if kind != first_type)]
    procedure = ClaimGenerator._sample_procedure(
        ctx, ctx.base_service_date, "INST" if first_type == "INST" else "PROF"
    )
    count_name = (
        "claims_per_encounter"
        if family == "encounter"
        else "claims_per_billing_episode"
    )
    count = sample_count(
        count_name,
        conditions=[
            (key, value)
            for key, value in (
                (f"{family}_setting_{count_name}", setting),
                (f"{family}_procedure_{count_name}", procedure),
            )
            if key in stats.correlations
        ],
        minimum=1,
    )
    count = max(1, len(required), count)
    if claim_limit is not None:
        count = min(count, claim_limit)
    types_list: list[ClaimType] = []
    for index in range(count):
        if index == 0:
            kind = first_type
        elif institutional_rate is not None:
            kind = "INST" if random.random() < institutional_rate else "PROF"
        elif index < len(required):
            kind = required[index]
        else:
            kind = _sample("claim_type", conditions, set(required) or {"INST", "PROF"})
        types_list.append("INST" if kind == "INST" else "PROF")
    return EpisodePlan(family, setting, tuple(types_list), procedure)


def generate_episode_claim(
    generator: ClaimGenerator,
    ctx: PatientContext,
    plan: EpisodePlan,
    index: int,
    service_date: date,
    *,
    submission_date: date | None = None,
) -> Claim:
    primary = plan.primary_procedure if index == 0 else None
    if plan.claim_types[index] == "INST":
        return generator.generate_institutional_claim(
            ctx=ctx,
            service_date=service_date,
            primary_procedure_code=primary,
            is_inpatient=(plan.setting == "inpatient")
            if plan.setting in {"inpatient", "outpatient", "office", "home"}
            else None,
            submission_date=submission_date,
        )
    setting_codes = {
        "inpatient": {"21"},
        "outpatient": {"22"},
        "facility": {"21", "22", "23", "24", "31", "32", "51", "52", "61"},
        "office": {"11", "49", "50", "71", "72"},
        "home": {"12", "13", "14", "15", "16", "33", "34"},
    }
    allowed = setting_codes.get(plan.setting, set()) & set(
        load_stats().distributions["place_of_service"]
    )
    pos = (
        _sample(
            "place_of_service", [("procedure_place_of_service", primary or "")], allowed
        )
        if allowed
        else None
    )
    return generator.generate_claim(
        ctx=ctx,
        service_date=service_date,
        primary_procedure_code=primary,
        place_of_service_code=pos,
    )

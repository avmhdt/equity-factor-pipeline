"""As-first-reported fundamentals keyed by filing acceptance (FR-08, DQ-02 to DQ-06).

Each row answers: this value, for this item and period, became public at ``available_at``.
A restated value is a new row with its own ``available_at``; nothing is overwritten.
"""

from __future__ import annotations

import polars as pl
import structlog

from eqpit.calendar import local_date
from eqpit.edgar.xbrl_map import DEFAULT_XBRL_MAP, XbrlMap, xbrl_map_frame
from eqpit.pit.filings import FORMS, assert_after_period_end, stamp_availability
from eqpit.pit.policy import PitPolicy
from eqpit.schemas import FUNDAMENTALS_PIT, conform

log = structlog.get_logger(__name__)

_KEY = ["company_id", "item", "period_end", "period_type"]
_FISCAL_INDEX = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 4}
_MONTHS = {"3m": 3, "6m": 6, "9m": 9, "12m": 12}
_DAYS_PER_QUARTER = 91.3


def _map_items(facts: pl.DataFrame, mapping: XbrlMap) -> pl.DataFrame:
    """Join tags to canonical items; for one fact the first tag present wins."""
    mapped = facts.join(xbrl_map_frame(mapping), on="tag", how="inner")
    return mapped.sort("priority", "tag", "value").unique(
        subset=["company_id", "item", "start", "end", "accession"],
        keep="first",
        maintain_order=True,
    )


def _classify_periods(facts: pl.DataFrame) -> pl.DataFrame:
    """Label each fact instant, 3m, 6m, 9m or 12m; other durations are dropped."""
    days = (pl.col("end") - pl.col("start")).dt.total_days()
    return facts.with_columns(
        pl.when(pl.col("start").is_null())
        .then(pl.lit("instant"))
        .when(days.is_between(80, 100))
        .then(pl.lit("3m"))
        .when(days.is_between(170, 190))
        .then(pl.lit("6m"))
        .when(days.is_between(260, 285))
        .then(pl.lit("9m"))
        .when(days.is_between(350, 380))
        .then(pl.lit("12m"))
        .otherwise(None)
        .alias("period_type")
    ).drop_nulls("period_type")


def _attach_availability(
    facts: pl.DataFrame, filings: pl.DataFrame, policy: PitPolicy
) -> pl.DataFrame:
    """Take ``available_at`` from the source filing, else fall back on the fact's dates."""
    linked = facts.join(
        filings.select("accession", "available_at", "pit_method"), on="accession", how="left"
    )
    known = linked.filter(pl.col("available_at").is_not_null())
    unlinked = linked.filter(pl.col("available_at").is_null()).drop("available_at", "pit_method")
    fallback = stamp_availability(unlinked.with_columns(pl.col("end").alias("period_end")), policy)
    return pl.concat([known, fallback.drop("period_end")], how="diagonal")


def _label_fiscal_period(facts: pl.DataFrame) -> pl.DataFrame:
    """Fiscal period of each fact, read from its own filing's ``fp`` and period end.

    ``fp`` describes the filing, not the fact, so comparatives step back from it. Only the
    fact's own filing is consulted, which keeps the label free of later information.
    """
    filing_end = pl.col("end").max().over(pl.coalesce("accession", pl.col("end").cast(pl.String)))
    quarters_back = (
        ((filing_end - pl.col("end")).dt.total_days() / _DAYS_PER_QUARTER).round().cast(pl.Int32)
    )
    index = pl.col("fp").replace_strict(
        list(_FISCAL_INDEX),
        list(_FISCAL_INDEX.values()),
        default=None,
        return_dtype=pl.Int32,
    )
    quarter = (index - quarters_back - 1) % 4 + 1
    return facts.with_columns(
        pl.when(pl.col("period_type").eq("12m"))
        .then(pl.lit("FY"))
        .when(pl.col("period_type").eq("9m"))
        .then(pl.lit("Q3"))
        .when(pl.col("period_type").eq("6m"))
        .then(pl.lit("Q2"))
        .when(pl.col("period_type").eq("instant") & quarter.eq(4))
        .then(pl.lit("FY"))
        .otherwise(pl.concat_str(pl.lit("Q"), quarter.cast(pl.String)))
        .alias("fiscal_period")
    )


def _derive_quarters(facts: pl.DataFrame) -> pl.DataFrame:
    """Derive 3m flows from year-to-date ones, as FY - 9M, 9M - 6M and 6M - 3M (DQ-06).

    A derived row exists from the moment both inputs are public. It is dropped only if a
    reported 3m value for that period was already public by then, so a value reported later
    never removes an earlier row.
    """
    durations = (
        facts.filter(pl.col("period_type").is_in(list(_MONTHS)))
        .with_columns(
            pl.col("period_type")
            .replace_strict(list(_MONTHS), list(_MONTHS.values()), return_dtype=pl.Int32)
            .alias("months")
        )
        .sort("available_at", "accession")
        .unique(
            subset=["company_id", "item", "start", "end", "value"],
            keep="first",
            maintain_order=True,
        )
    )
    prior = durations.select(
        "company_id",
        "item",
        "start",
        (pl.col("months") + 3).alias("months"),
        pl.col("value").alias("prior_value"),
        pl.col("available_at").alias("prior_at"),
        pl.col("accession").alias("prior_accession"),
    )
    pairs = (
        durations.filter(pl.col("months") >= 6)
        .join(prior, on=["company_id", "item", "start", "months"], how="inner")
        .with_columns(pl.max_horizontal("available_at", "prior_at").alias("at"))
        .sort("available_at", "prior_at", "accession", "prior_accession")
        .unique(subset=["company_id", "item", "end", "at"], keep="last", maintain_order=True)
    )
    reported = (
        durations.filter(pl.col("months") == 3)
        .group_by("company_id", "item", "end")
        .agg(pl.col("available_at").min().alias("reported_at"))
    )
    derived = (
        pairs.join(reported, on=["company_id", "item", "end"], how="left")
        .filter(pl.col("reported_at").is_null() | (pl.col("reported_at") > pl.col("at")))
        .with_columns(
            (pl.col("value") - pl.col("prior_value")).alias("value"),
            pl.col("at").alias("available_at"),
            pl.lit("3m").alias("period_type"),
            pl.format("derived:{}m-{}m:{}", pl.col("months"), pl.col("months") - 3, "tag").alias(
                "source_tag"
            ),
            pl.format("Q{}", (pl.col("months") / 3).cast(pl.Int32)).alias("fiscal_period"),
        )
    )
    return derived.select(facts.columns)


def _add_book_equity(facts: pl.DataFrame) -> pl.DataFrame:
    """Add ``book_equity`` per filing: equity + deferred taxes - preferred (Fama-French)."""
    instants = facts.filter(pl.col("period_type").eq("instant"))
    keys = ["company_id", "end", "accession"]
    equity = instants.filter(pl.col("item").eq("stockholders_equity"))
    extras = instants.filter(pl.col("item").is_in(["deferred_taxes", "preferred_stock"])).select(
        *keys, "item", "value"
    )
    taxes = extras.filter(pl.col("item").eq("deferred_taxes")).select(
        *keys, pl.col("value").alias("taxes")
    )
    preferred = extras.filter(pl.col("item").eq("preferred_stock")).select(
        *keys, pl.col("value").alias("preferred")
    )
    book = (
        equity.join(taxes, on=keys, how="left", nulls_equal=True)
        .join(preferred, on=keys, how="left", nulls_equal=True)
        .with_columns(
            (
                pl.col("value")
                + pl.col("taxes").fill_null(0.0)
                - pl.col("preferred").fill_null(0.0)
            ).alias("value"),
            pl.lit("book_equity").alias("item"),
            pl.lit("derived:book_equity").alias("source_tag"),
        )
        .select(facts.columns)
    )
    return pl.concat([facts, book], how="vertical")


def _keep_value_changes(facts: pl.DataFrame) -> pl.DataFrame:
    """Keep the first report of each period and every later change of its value."""
    ordered = facts.sort(*_KEY, "available_at", "accession", "value")
    changed = ordered.filter(pl.col("value").ne_missing(pl.col("value").shift(1).over(_KEY)))
    return changed.with_columns(
        (pl.int_range(pl.len()).over(_KEY) > 0).alias("is_restatement"),
    )


def build_fundamentals(
    facts: pl.DataFrame,
    filings: pl.DataFrame,
    policy: PitPolicy,
    mapping: XbrlMap = DEFAULT_XBRL_MAP,
) -> pl.DataFrame:
    """Build ``fundamentals_pit`` from long-format facts.

    Args:
        facts: one row per fact, with ``cik``, ``company_id``, ``tag``, ``start`` (null for an
            instant), ``end``, ``value``, ``unit``, ``accession``, ``form``, ``filed`` and
            ``fp``, the source filing's fiscal period.
        filings: the ``filings`` table.
        policy: availability rules.
        mapping: canonical item to ordered tags.

    Returns:
        The long table, sorted. Raises if any row is available by its period end.
    """
    wanted = facts.filter(pl.col("form").is_in(FORMS))
    typed = _classify_periods(_map_items(wanted, mapping))
    stamped = _attach_availability(typed, filings, policy).with_columns(
        pl.col("tag").alias("source_tag")
    )
    in_order = stamped.filter(local_date(pl.col("available_at")) > pl.col("end"))
    dropped = stamped.height - in_order.height
    if dropped:
        log.warning("facts_dated_after_their_filing", rows=dropped)
    labelled = _label_fiscal_period(in_order)
    combined = pl.concat([labelled, _derive_quarters(labelled)], how="vertical")
    built = _keep_value_changes(
        _add_book_equity(combined).with_columns(pl.col("end").alias("period_end"))
    )
    assert_after_period_end(built)
    return conform(built, FUNDAMENTALS_PIT).sort(*_KEY, "available_at", "accession", "value")

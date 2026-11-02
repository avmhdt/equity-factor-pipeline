"""Point-in-time table schemas (spec section 7b)."""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

INSTANT = pl.Datetime("ns", "UTC")

Schema = Mapping[str, pl.DataType | type[pl.DataType]]

SECURITIES: Schema = {
    "security_id": pl.String,
    "company_id": pl.String,
    "cik": pl.Int64,
    "gvkey": pl.String,
    "permno": pl.Int64,
    "permaticker": pl.Int64,
    "ticker": pl.String,
    "name": pl.String,
    "exchange": pl.String,
    "share_type": pl.String,
    "sic": pl.Int16,
    "ff49": pl.Int16,
    "ff12": pl.Int16,
    "valid_from": pl.Date,
    "valid_to": pl.Date,
    "available_at": INSTANT,
}

PRICES_DAILY: Schema = {
    "security_id": pl.String,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "dollar_volume": pl.Float64,
    "ret": pl.Float64,
    "split_factor": pl.Float64,
    "shares_out": pl.Float64,
    "mcap": pl.Float64,
    "price_flag": pl.UInt8,
    "available_at": INSTANT,
}

DELISTINGS: Schema = {
    "security_id": pl.String,
    "delist_date": pl.Date,
    "delist_code": pl.Int16,
    "reason": pl.String,
    "exchange_at_delist": pl.String,
    "dlret_raw": pl.Float64,
    "dlret": pl.Float64,
    "imputation_rule": pl.String,
    "available_at": INSTANT,
}

CORP_ACTIONS: Schema = {
    "security_id": pl.String,
    "ex_date": pl.Date,
    "action": pl.String,
    "value": pl.Float64,
    "new_ticker": pl.String,
    "available_at": INSTANT,
}

FILINGS: Schema = {
    "cik": pl.Int64,
    "accession": pl.String,
    "form": pl.String,
    "period_end": pl.Date,
    "filed": pl.Date,
    "acceptance_dt": INSTANT,
    "available_at": INSTANT,
    "pit_method": pl.String,
}

FUNDAMENTALS_PIT: Schema = {
    "company_id": pl.String,
    "cik": pl.Int64,
    "gvkey": pl.String,
    "item": pl.String,
    "period_end": pl.Date,
    "fiscal_period": pl.String,
    "period_type": pl.String,
    "value": pl.Float64,
    "unit": pl.String,
    "accession": pl.String,
    "form": pl.String,
    "available_at": INSTANT,
    "pit_method": pl.String,
    "is_restatement": pl.Boolean,
    "source_tag": pl.String,
}

UNIVERSE_MONTHLY: Schema = {
    "asof": pl.Date,
    "security_id": pl.String,
    "company_id": pl.String,
    "company_mcap": pl.Float64,
    "mcap": pl.Float64,
    "rank": pl.Int32,
    "in_top_1000": pl.Boolean,
    "in_top_3000": pl.Boolean,
    "exchange": pl.String,
    "ff49": pl.Int16,
    "delisted_after": pl.Date,
    "available_at": INSTANT,
}

FORWARD_RETURNS: Schema = {
    "asof": pl.Date,
    "security_id": pl.String,
    "h": pl.Int8,
    "fwd_ret": pl.Float64,
    "fwd_ret_excess": pl.Float64,
}

SIGNALS: Schema = {
    "asof": pl.Date,
    "security_id": pl.String,
    "signal": pl.String,
    "raw": pl.Float64,
    "winsorized": pl.Float64,
    "z": pl.Float64,
    "z_neutral": pl.Float64,
    "input_available_at": INSTANT,
}

TABLES: Mapping[str, Schema] = {
    "securities": SECURITIES,
    "prices_daily": PRICES_DAILY,
    "delistings": DELISTINGS,
    "corp_actions": CORP_ACTIONS,
    "filings": FILINGS,
    "fundamentals_pit": FUNDAMENTALS_PIT,
    "universe_monthly": UNIVERSE_MONTHLY,
    "forward_returns": FORWARD_RETURNS,
}

GATED_TABLES = (
    "securities",
    "prices_daily",
    "delistings",
    "corp_actions",
    "filings",
    "fundamentals_pit",
    "universe_monthly",
)
"""Tables whose rows all carry ``available_at`` (DQ-01)."""


class SchemaError(ValueError):
    """A frame does not match its table schema."""


def conform(frame: pl.DataFrame, schema: Schema) -> pl.DataFrame:
    """Select and cast the schema's columns, in order; absent nullable columns become null.

    Raises ``SchemaError`` when a required key column (the first two) is missing.
    """
    keys = list(schema)[:2]
    missing_keys = [name for name in keys if name not in frame.columns]
    if missing_keys:
        raise SchemaError(f"missing key columns {missing_keys}")
    columns = [
        pl.col(name).cast(dtype, strict=True)
        if name in frame.columns
        else pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in schema.items()
    ]
    return frame.select(columns)

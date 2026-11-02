"""External benchmark files: Kenneth French's factors and OSAP portfolios (DQ-14)."""

from __future__ import annotations

import io
import re
import ssl
import zipfile
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from eqpit import __version__, calendar
from eqpit.snapshot import (
    SnapshotManifest,
    latest_snapshot,
    list_snapshots,
    open_snapshot,
    read_manifest,
)
from eqpit.throttle import ByteThrottle

FRENCH_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
FRENCH_FILES = {
    "ff5_monthly": "F-F_Research_Data_5_Factors_2x3_CSV.zip",
    "mom_monthly": "F-F_Momentum_Factor_CSV.zip",
}
OSAP_RELEASE = "Data-Release-2026.10"
OSAP_BASE = f"https://huggingface.co/datasets/OpenSourceAP/CrossSection/resolve/{OSAP_RELEASE}"
OSAP_FILES = {
    "PredictorLSretWide.parquet": "Portfolios/Full%20Sets%20OP/PredictorLSretWide.parquet",
    "SignalDoc.csv": "SignalDoc.csv",
}
_MONTH_ROW = re.compile(r"^\s*(\d{6})\s*,")
_MISSING = (-99.99, -999.0)


def parse_french_monthly(text: str) -> pl.DataFrame:
    """Parse the monthly block of a French CSV into decimals keyed by month end.

    The file holds a preamble, a header, monthly rows, then an annual block. The monthly block
    is the first run of ``YYYYMM`` rows. Percent values become decimals (DQ-14), and the
    -99.99 missing marker becomes null.
    """
    lines = text.splitlines()
    first = next(index for index, line in enumerate(lines) if _MONTH_ROW.match(line))
    header = [cell.strip() for cell in lines[first - 1].split(",")]
    names = [name.lower().replace("-", "_") for name in header[1:]]
    rows: list[list[float | int | None]] = []
    for line in lines[first:]:
        if not _MONTH_ROW.match(line):
            break
        cells = line.split(",")
        values = [float(cell) for cell in cells[1 : len(names) + 1]]
        rows.append(
            [int(cells[0]), *[None if value in _MISSING else value / 100.0 for value in values]]
        )
    frame = pl.DataFrame(
        rows, schema={"yyyymm": pl.Int64, **dict.fromkeys(names, pl.Float64)}, orient="row"
    )
    return frame.with_columns(
        pl.date(pl.col("yyyymm") // 100, pl.col("yyyymm") % 100, 1)
        .dt.month_end()
        .alias("calendar_month")
    ).drop("yyyymm")


def align_to_sessions(frame: pl.DataFrame) -> pl.DataFrame:
    """Key a monthly frame by the month's last session instead of its calendar end."""
    first, last = frame["calendar_month"].min(), frame["calendar_month"].max()
    assert isinstance(first, date)
    assert isinstance(last, date)
    ends = calendar.month_ends(first.replace(day=1), last)
    table = pl.DataFrame({"month": ends}, schema={"month": pl.Date}).with_columns(
        pl.col("month").dt.month_end().alias("calendar_month")
    )
    return frame.join(table, on="calendar_month", how="inner").drop("calendar_month").sort("month")


def _system_tls() -> ssl.SSLContext:
    """Verify against the operating system's trust store, as curl does here."""
    return ssl.create_default_context()


def pull_osap(dest: Path, transport: httpx.BaseTransport | None = None) -> SnapshotManifest:
    """Download published long-short returns and SignalDoc (D-09: no firm-level data)."""
    with (
        httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
            verify=_system_tls(),
        ) as client,
        open_snapshot(
            dest,
            "osap",
            "portfolios",
            False,
            source=OSAP_BASE,
            package_version=__version__,
            metadata={"release": OSAP_RELEASE},
        ) as staging,
    ):
        for name, path in OSAP_FILES.items():
            throttle = ByteThrottle()
            with client.stream("GET", f"{OSAP_BASE}/{path}") as stream:
                stream.raise_for_status()
                with (staging / name).open("wb") as handle:
                    for chunk in stream.iter_bytes(chunk_size=1 << 18):
                        handle.write(chunk)
                        throttle.consumed(len(chunk))
    return read_manifest(latest_snapshot(dest, "portfolios"))


def load_signal_doc(root: Path) -> pl.DataFrame:
    """OSAP's table of predictors, conventions, sample periods and publication years."""
    return pl.read_csv(
        latest_snapshot(root, "portfolios") / "SignalDoc.csv", infer_schema_length=None
    )


def load_osap_returns(root: Path) -> pl.DataFrame:
    """Published long-short returns as decimals: ``month``, ``target``, ``ret``.

    The file is in percent and keyed by calendar month, so months align to the last session.
    """
    wide = pl.read_parquet(latest_snapshot(root, "portfolios") / "PredictorLSretWide.parquet")
    long = wide.unpivot(index="date", variable_name="target", value_name="ret").drop_nulls("ret")
    keyed = long.with_columns(
        pl.col("date").cast(pl.Date).dt.month_end().alias("calendar_month"),
        (pl.col("ret") / 100.0).alias("ret"),
    ).drop("date")
    return align_to_sessions(keyed).select("month", "target", "ret").sort("target", "month")


def pull_french(dest: Path, transport: httpx.BaseTransport | None = None) -> SnapshotManifest:
    """Download the French zips into a new snapshot."""
    with (
        httpx.Client(
            transport=transport, timeout=httpx.Timeout(60.0), follow_redirects=True
        ) as client,
        open_snapshot(
            dest,
            "french",
            "factors",
            False,
            source=FRENCH_BASE,
            package_version=__version__,
        ) as staging,
    ):
        for name, filename in FRENCH_FILES.items():
            response = client.get(f"{FRENCH_BASE}/{filename}")
            response.raise_for_status()
            (staging / f"{name}.zip").write_bytes(response.content)
    return read_manifest(latest_snapshot(dest, "factors"))


def _read_zip(path: Path) -> str:
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as bundle:
        return bundle.read(bundle.namelist()[0]).decode("latin-1")


def load_french(root: Path) -> pl.DataFrame:
    """Monthly ``mkt_rf``, ``smb``, ``hml``, ``rmw``, ``cma``, ``rf`` and ``umd`` by session."""
    snapshot = latest_snapshot(root, "factors")
    five = parse_french_monthly(_read_zip(snapshot / "ff5_monthly.zip"))
    momentum = parse_french_monthly(_read_zip(snapshot / "mom_monthly.zip"))
    momentum = momentum.rename({momentum.columns[0]: "umd"})
    joined = five.join(momentum, on="calendar_month", how="left")
    return align_to_sessions(joined)


def load_risk_free(root: Path) -> pl.LazyFrame | None:
    """Monthly ``month`` and ``rf`` from French's file, when a snapshot exists."""
    if not list_snapshots(root, "factors"):
        return None
    return load_french(root).select("month", "rf").drop_nulls("rf").lazy()

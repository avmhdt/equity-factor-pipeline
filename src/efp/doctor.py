"""Environment and data-access checks behind ``efp doctor``."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from eqpit.edgar.client import EdgarClient, EdgarConfigError, validate_user_agent
from eqpit.providers.sharadar import TABLES, fetch_one_row
from eqpit.settings import Settings

Status = Literal["ok", "warn", "fail"]

MIN_FREE_GB = 60.0
CHECK_CIK = "0000320193"
FRENCH_FF5_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_5_Factors_2x3_CSV.zip"
)


@dataclass(frozen=True)
class Check:
    """One line of the doctor report."""

    name: str
    status: Status
    detail: str


def check_python() -> Check:
    """Python 3.12 or newer."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return Check("python", "ok" if sys.version_info >= (3, 12) else "fail", version)


def check_uv() -> Check:
    """uv on PATH."""
    executable = shutil.which("uv")
    if executable is None:
        return Check("uv", "fail", "uv not on PATH")
    output = subprocess.run([executable, "--version"], capture_output=True, text=True, check=False)
    return Check("uv", "ok", output.stdout.strip())


def check_user_agent(settings: Settings) -> Check:
    """SEC_USER_AGENT present and carrying an email; the value itself is not printed."""
    try:
        validate_user_agent(settings.sec_user_agent)
    except EdgarConfigError as error:
        return Check("sec_user_agent", "fail", str(error))
    return Check("sec_user_agent", "ok", "set, with a contact email")


def check_sharadar_key(settings: Settings) -> Check:
    """SHARADAR_API_KEY presence; only its length is ever reported."""
    length = len(settings.sharadar_api_key.get_secret_value())
    if length == 0:
        return Check("sharadar_api_key", "warn", "not set; needed before M3 (P-07)")
    return Check("sharadar_api_key", "ok", f"set ({length} characters)")


def check_data_root(settings: Settings) -> list[Check]:
    """Data root resolved, never on C:, with enough free space."""
    root = settings.data_dir.resolve()
    probe = next((path for path in (root, *root.parents) if path.exists()), root.anchor)
    free_gb = shutil.disk_usage(probe).free / 1e9
    on_c = root.drive.upper() == "C:"
    return [
        Check("data_root", "fail" if on_c else "ok", f"{root}" + (" is on C:" if on_c else "")),
        Check(
            "disk_free",
            "ok" if free_gb >= MIN_FREE_GB else "warn",
            f"{free_gb:,.0f} GB free, {MIN_FREE_GB:.0f} GB wanted (P-08)",
        ),
    ]


def check_stooq(settings: Settings) -> Check:
    """Owner's Stooq snapshot, used only by the dev-only free path."""
    if settings.stooq_dir is None:
        return Check("stooq_dir", "warn", "STOOQ_DIR not set; edgar_free prices need it")
    if not Path(settings.stooq_dir).is_dir():
        return Check("stooq_dir", "warn", f"{settings.stooq_dir} not found")
    return Check("stooq_dir", "ok", str(settings.stooq_dir))


def check_edgar(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> tuple[Check, Check]:
    """Fetch one companyfacts and one submissions JSON."""
    urls = {
        "edgar_companyfacts": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CHECK_CIK}.json",
        "edgar_submissions": f"https://data.sec.gov/submissions/CIK{CHECK_CIK}.json",
    }
    results: list[Check] = []
    try:
        client = EdgarClient.from_settings(settings, transport=transport)
    except EdgarConfigError as error:
        return (
            Check("edgar_companyfacts", "fail", str(error)),
            Check("edgar_submissions", "fail", str(error)),
        )
    with client:
        for name, url in urls.items():
            try:
                payload = client.get_json(url)
            except httpx.HTTPError as error:
                results.append(Check(name, "fail", str(error)))
                continue
            entity = payload.get("entityName") or payload.get("name", "")
            results.append(Check(name, "ok", f"CIK {CHECK_CIK}: {entity}"))
    return results[0], results[1]


def check_french(transport: httpx.BaseTransport | None = None) -> Check:
    """Fetch the French FF5 zip and confirm it opens."""
    try:
        with httpx.Client(
            transport=transport, timeout=httpx.Timeout(60.0), follow_redirects=True
        ) as client:
            response = client.get(FRENCH_FF5_URL)
            response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            members = archive.namelist()
    except (httpx.HTTPError, zipfile.BadZipFile) as error:
        return Check("french_ff5", "fail", str(error))
    return Check("french_ff5", "ok", f"{len(response.content):,} bytes, {members[0]}")


def check_sharadar_access(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> list[Check]:
    """One-row query per table. Skipped with a warning until the key is set."""
    api_key = settings.sharadar_api_key.get_secret_value()
    if not api_key:
        return [Check("sharadar_access", "warn", "skipped: SHARADAR_API_KEY not set")]
    checks: list[Check] = []
    for dataset, table in TABLES.items():
        try:
            body = fetch_one_row(table, api_key, transport=transport)
        except httpx.HTTPError as error:
            status_code = getattr(getattr(error, "response", None), "status_code", "network")
            checks.append(Check(f"sharadar_{dataset}", "fail", f"HTTP {status_code}"))
            continue
        rows = max(len(body.strip().splitlines()) - 1, 0)
        status: Status = "ok" if rows >= 1 else "fail"
        checks.append(Check(f"sharadar_{dataset}", status, f"table {table}: {rows} row"))
    return checks


def run_doctor(
    settings: Settings,
    network: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> list[Check]:
    """Run every check, with live access checks when ``network`` is set."""
    checks = [
        check_python(),
        check_uv(),
        check_user_agent(settings),
        check_sharadar_key(settings),
        *check_data_root(settings),
        check_stooq(settings),
    ]
    if network:
        checks.extend(check_edgar(settings, transport))
        checks.append(check_french(transport))
        checks.extend(check_sharadar_access(settings, transport))
    return checks

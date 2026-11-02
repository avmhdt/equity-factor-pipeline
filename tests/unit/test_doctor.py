"""``efp doctor`` reports each check without leaking secrets."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from efp.doctor import Check, check_data_root, check_sharadar_key, check_stooq, run_doctor
from efp.settings import Settings

AGENT = "Test Person test.person@example.com"


def french_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("F-F_Research_Data_5_Factors_2x3.csv", "x")
    return buffer.getvalue()


def handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "data.sec.gov":
        return httpx.Response(200, json={"entityName": "Apple Inc.", "name": "Apple Inc."})
    if host == "mba.tuck.dartmouth.edu":
        return httpx.Response(200, content=french_zip())
    return httpx.Response(200, text="ticker,date\nAAPL,2026-10-30\n")


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {check.name: check for check in checks}


def settings(tmp_path: Path, **values: object) -> Settings:
    base: dict[str, object] = {"sec_user_agent": AGENT, "efp_data_dir": str(tmp_path)}
    return Settings(_env_file=None, **(base | values))  # type: ignore[call-arg, arg-type]


def test_network_checks_pass_with_a_key(tmp_path: Path) -> None:
    config = settings(tmp_path, sharadar_api_key=SecretStr("secret"))
    checks = by_name(run_doctor(config, network=True, transport=httpx.MockTransport(handler)))
    assert checks["edgar_companyfacts"].status == "ok"
    assert checks["edgar_submissions"].status == "ok"
    assert checks["french_ff5"].status == "ok"
    for dataset in ("SEP", "SF1", "TICKERS", "ACTIONS"):
        assert checks[f"sharadar_{dataset}"].status == "ok"


def test_sharadar_is_skipped_with_a_warning_until_the_key_is_set(tmp_path: Path) -> None:
    config = settings(tmp_path)
    checks = by_name(run_doctor(config, network=True, transport=httpx.MockTransport(handler)))
    assert checks["sharadar_api_key"].status == "warn"
    assert checks["sharadar_access"].status == "warn"
    assert "sharadar_SEP" not in checks


def test_refused_key_fails_and_never_prints_the_key(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.sharadar.com":
            return httpx.Response(403)
        return handler(request)

    config = settings(tmp_path, sharadar_api_key=SecretStr("very-secret"))
    checks = run_doctor(config, network=True, transport=httpx.MockTransport(refuse))
    assert by_name(checks)["sharadar_SEP"].status == "fail"
    assert all("very-secret" not in check.detail for check in checks)


def test_edgar_failure_is_reported(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403))
    checks = by_name(run_doctor(settings(tmp_path), network=True, transport=transport))
    assert checks["edgar_submissions"].status == "fail"
    assert checks["french_ff5"].status == "fail"


def test_missing_user_agent_fails(tmp_path: Path) -> None:
    config = settings(tmp_path, sec_user_agent="")
    checks = by_name(run_doctor(config, network=True, transport=httpx.MockTransport(handler)))
    assert checks["sec_user_agent"].status == "fail"
    assert checks["edgar_companyfacts"].status == "fail"


def test_key_length_only(tmp_path: Path) -> None:
    check = check_sharadar_key(settings(tmp_path, sharadar_api_key=SecretStr("abcd")))
    assert check.status == "ok"
    assert "abcd" not in check.detail


def test_data_root_on_c_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    drive = Path(r"C:\data\efp")
    config = settings(tmp_path)
    monkeypatch.setattr(Settings, "data_dir", property(lambda self: drive))
    if drive.drive != "C:":
        pytest.skip("Windows path semantics")
    assert check_data_root(config)[0].status == "fail"


def test_stooq_states(tmp_path: Path) -> None:
    assert check_stooq(settings(tmp_path)).status == "warn"
    assert check_stooq(settings(tmp_path, stooq_dir=tmp_path / "nope")).status == "warn"
    assert check_stooq(settings(tmp_path, stooq_dir=tmp_path)).status == "ok"

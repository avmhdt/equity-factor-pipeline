"""Placeholder so the required leakage job runs from M1; real tests arrive in M2."""

from __future__ import annotations

import pytest


@pytest.mark.leakage
def test_leakage_job_runs() -> None:
    assert True

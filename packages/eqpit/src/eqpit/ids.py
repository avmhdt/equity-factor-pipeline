"""Permanent identifiers. Tickers are attributes, never keys."""

from __future__ import annotations

import re

_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")


def crsp_id(permno: int) -> str:
    """Security id for a CRSP PERMNO."""
    return f"CRSP:{permno}"


def sharadar_id(permaticker: int) -> str:
    """Security id for a Sharadar permaticker."""
    return f"SHR:{permaticker}"


def edgar_id(cik: int, share_class: str = "common") -> str:
    """Security id for the free path: CIK plus share class."""
    return f"EDGAR:{cik}:{share_class}"


def synthetic_id(number: int) -> str:
    """Security id on the synthetic market."""
    return f"SYN:{number}"


def company_from_cik(cik: int) -> str:
    """Issuer key built from a CIK."""
    return f"CIK:{cik}"


def cik10(cik: int | str) -> str:
    """Ten-digit zero-padded CIK, as EDGAR URLs need."""
    return f"{int(cik):010d}"


def is_accession(value: str) -> bool:
    """True for the ``0000320193-24-000123`` form."""
    return _ACCESSION.fullmatch(value) is not None

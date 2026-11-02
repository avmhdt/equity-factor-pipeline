"""Canonical fundamental items and their ordered XBRL tags (DQ-06).

``configs/xbrl_map.yaml`` is the editable copy for experiments; a test keeps it equal to this
default, which exists because eqpit does not depend on a YAML parser (NFR-12).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import polars as pl

XbrlMap = Mapping[str, Sequence[str]]

DEFAULT_XBRL_MAP: XbrlMap = {
    "total_assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "inventory": ["InventoryNet"],
    "ppe_gross": ["PropertyPlantAndEquipmentGross"],
    "ppe_net": ["PropertyPlantAndEquipmentNet"],
    "total_liabilities": ["Liabilities"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "short_term_debt": ["DebtCurrent", "ShortTermBorrowings", "LongTermDebtCurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "income_taxes_payable": ["AccruedIncomeTaxesCurrent", "TaxesPayableCurrent"],
    "deferred_taxes": ["DeferredIncomeTaxLiabilitiesNet", "DeferredTaxLiabilitiesNoncurrent"],
    "stockholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "preferred_stock": ["PreferredStockValue"],
    "minority_interest": ["MinorityInterest"],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "interest_expense": ["InterestExpense"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "income_before_extra": [
        "IncomeLossFromContinuingOperations",
        "NetIncomeLoss",
    ],
    "depreciation": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "shares_out": ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"],
}


def xbrl_map_frame(mapping: XbrlMap = DEFAULT_XBRL_MAP) -> pl.DataFrame:
    """One row per ``(tag, item)`` with the tag's priority; lower wins.

    One tag may serve several items, as ``NetIncomeLoss`` does.
    """
    rows = [
        (tag, item, priority) for item, tags in mapping.items() for priority, tag in enumerate(tags)
    ]
    return pl.DataFrame(
        rows,
        schema={"tag": pl.String, "item": pl.String, "priority": pl.Int32},
        orient="row",
    )


def all_tags(mapping: XbrlMap = DEFAULT_XBRL_MAP) -> frozenset[str]:
    """Every XBRL tag the map uses."""
    return frozenset(tag for tags in mapping.values() for tag in tags)

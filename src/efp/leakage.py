"""The static half of the leakage test (FR-12); it lives outside ``efp.signals`` on purpose."""

from __future__ import annotations

import ast
import struct
from pathlib import Path

import polars as pl

from eqpit.calendar import closes_on

ALLOWED_IMPORT_ROOTS = frozenset(
    {"collections", "dataclasses", "datetime", "numpy", "polars", "typing"}
)
ALLOWED_MODULES = frozenset({"eqpit.store", "__future__"})
FORBIDDEN_NAMES = frozenset(
    {"read_parquet", "scan_parquet", "read_csv", "scan_csv", "LabelStore", "forward_returns"}
)


class LeakageError(AssertionError):
    """A feature used data published after its rebalance date."""


def late_rows(panel: pl.DataFrame) -> pl.DataFrame:
    """Rows whose ``input_available_at`` falls after ``cutoff(asof)``."""
    cutoffs = closes_on(panel["asof"]).alias("cutoff")
    return panel.with_columns(cutoffs).filter(pl.col("input_available_at") > pl.col("cutoff"))


def assert_no_feature_after_cutoff(panel: pl.DataFrame) -> None:
    """Raise ``LeakageError`` if any row used data published after its cutoff."""
    late = late_rows(panel)
    if not late.is_empty():
        signals = sorted(late["signal"].unique().to_list())
        raise LeakageError(f"{late.height} rows after the cutoff in {signals}")


def _bits(value: float | None) -> bytes:
    return b"" if value is None else struct.pack("<d", value)


def _describe_changes(left: pl.DataFrame, right: pl.DataFrame, moved: list[int]) -> str:
    """Per signal and date, how many values moved and the largest relative move."""
    rows = left.select("signal", "asof", pl.col("raw").alias("before")).with_columns(
        right["raw"].alias("after")
    )[moved]
    summary = (
        rows.with_columns(
            ((pl.col("after") - pl.col("before")).abs() / pl.col("before").abs()).alias("move")
        )
        .group_by("signal", "asof", maintain_order=True)
        .agg(pl.len().alias("values"), pl.col("move").max().alias("largest"))
    )
    return "; ".join(
        f"{signal} {asof}: {values} values, largest relative move {largest:.3g}"
        for signal, asof, values, largest in summary.iter_rows()
    )


def assert_identical(baseline: pl.DataFrame, perturbed: pl.DataFrame) -> None:
    """Raise ``LeakageError`` unless two signal panels are bit-identical."""
    keys = ["signal", "asof", "security_id"]
    left = baseline.sort(keys)
    right = perturbed.sort(keys)
    if left.height != right.height or left.select(keys).to_dict(as_series=False) != right.select(
        keys
    ).to_dict(as_series=False):
        raise LeakageError("perturbing later data changed which rows exist")
    values = zip(left["raw"].to_list(), right["raw"].to_list(), strict=True)
    moved = [index for index, (a, b) in enumerate(values) if _bits(a) != _bits(b)]
    if moved:
        raise LeakageError(
            f"perturbing later data changed {len(moved)} earlier signal values: "
            f"{_describe_changes(left, right, moved)}"
        )


def _is_unsorted_sum(node: ast.Call) -> bool:
    """A grouped float sum varies in its last bits between runs unless its values are sorted.

    Polars samples each frame to choose a group-by path, and a partitioned path adds the same
    numbers in a different grouping. About one evaluation in a thousand takes it.
    """
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "sum":
        return False
    receiver = node.func.value
    return not (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Attribute)
        and receiver.func.attr == "sort"
    )


def _is_unordered_group_by(node: ast.Call) -> bool:
    """Without ``maintain_order`` a group's rows and so its float sums can change between runs."""
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "group_by":
        return False
    return not any(
        keyword.arg == "maintain_order"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in node.keywords
    )


def import_violations(path: Path) -> list[str]:
    """Imports or names in a signal module that could bypass PitStore or break determinism."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
            found.extend(
                f"{path.name}: imports {alias.name}"
                for alias in node.names
                if alias.name in FORBIDDEN_NAMES
            )
        for module in modules:
            root = module.split(".")[0]
            allowed = (
                module in ALLOWED_MODULES
                or module.startswith("efp.signals")
                or root in ALLOWED_IMPORT_ROOTS
            )
            if not allowed:
                found.append(f"{path.name}: imports {module}")
        if isinstance(node, ast.Call) and _is_unordered_group_by(node):
            found.append(f"{path.name}: group_by without maintain_order=True")
        if isinstance(node, ast.Call) and _is_unsorted_sum(node):
            found.append(f"{path.name}: sum without sort()")
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(f"{path.name}: uses {node.attr}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"{path.name}: uses {node.id}")
    return found

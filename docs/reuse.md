# Reusing eqpit

`eqpit` is the point-in-time data layer on its own. It has no dependency on `efp`, `statsmodels` or `scikit-learn`, and sibling projects can use it directly.

```bash
uv add "eqpit @ git+https://github.com/avmhdt/equity-factor-pipeline#subdirectory=packages/eqpit"
```

## Example

This builds a small synthetic market and reads it as of a date. It needs no data or keys, and CI runs it on Ubuntu and Windows with `eqpit` installed alone.

```python
import tempfile
from datetime import date
from pathlib import Path

from eqpit.pit.build import build_pit
from eqpit.providers.synthetic import SyntheticProvider, write_market
from eqpit.store import LabelAccessError, PitStore
from eqpit.synthetic import SyntheticConfig

root = Path(tempfile.mkdtemp())
write_market(SyntheticConfig(seed=7, n_securities=60, years=4, start_year=2015), root / "raw")
build_pit(SyntheticProvider(root / "raw"), root / "pit")

store = PitStore.open(root / "pit")
asof = store.month_ends(date(2017, 1, 1), date(2017, 12, 31))[-1]

universe = store.universe(asof, 20)
assert universe.height == 20
print(universe.select("security_id", "rank", "mcap").head(3))

prices = store.prices(asof, 21, universe["security_id"].to_list())
assert prices["date"].max() <= asof

assets = store.fundamentals(asof, ["total_assets"])
assert (assets["available_at"] <= store.cutoff(asof)).all()

view = store.for_signals(asof)
try:
    view.labels()
except LabelAccessError:
    print("a signal view refuses forward returns")
else:
    raise AssertionError("labels should be refused")
```

## The contract

- Every row has `available_at`, in UTC. A read at date T returns a row only if `available_at` is at or before the close of T.
- `store.for_signals(asof)` is the view to hand a signal function. It raises on `labels()` and on any later date.
- `store.labels()` returns evaluation data. Keep it away from signal code.
- Fundamentals belong to issuers, so reads take `company_id`. Prices belong to securities and take `security_id`. Never join on tickers.
- Frames come back in one chunk, so a float sum can't depend on rows a filter removed.

## With real data

Pull snapshots and build the tables, as in [data.md](data.md), then open `PitStore` on the output directory. How each availability time is set is in [pit.md](pit.md).

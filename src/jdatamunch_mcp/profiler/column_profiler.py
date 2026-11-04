"""Single-pass streaming column profiler.

Processes rows one at a time using per-column accumulators.
Designed to work with index_local.py's main loop where profiling
and SQLite loading happen in the same pass over the data.

V2 statistical guarantees:
  * Welford online mean + Neumaier-compensated sum (numeric stability)
  * t-digest streaming quantiles (p01/p25/p50/p75/p95/p99) — bounded memory
  * HyperLogLog approximate cardinality once value-count cap exceeded
  * Per-column type-inference confidence + violation samples
  * Semantic type detection (email, url, uuid, etc.) post-primitive
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .tdigest import TDigest
from .hll import HyperLogLog
from .semantic_types import detect_semantic_type

# Version of the profiling rules. Bump it whenever a change alters the profile
# an unchanged file would produce, so existing indexes are re-profiled (see
# DataStore.needs_reindex). History:
#   1: everything before 2026-11-04.
#   2: datetime min/max compared as dates, not text; 12-hour AM/PM format
#      detected.
PROFILE_VERSION = 2

_NULL_VALUES = frozenset([
    "", "null", "NULL", "none", "None", "N/A", "n/a", "NA", "na",
    "NaN", "nan", "-", ".", "#N/A", "#NA", "#NULL!", "n.a.", "N.A.",
])

# Type rank: lower = more specific
_TYPE_RANK = {"integer": 0, "float": 1, "datetime": 2, "string": 3}
_TYPE_FROM_RANK = {0: "integer", 1: "float", 2: "datetime", 3: "string"}

MAX_CARDINALITY_TRACK = 5_000   # stop adding new keys to value_counts after this
SAMPLE_SIZE = 10                 # distinct non-null samples to collect
TDIGEST_DELTA = 100              # ~3KB per numeric column

VALUE_INDEX_CARDINALITY_LIMIT = 1_000  # full value map stored if cardinality <= this
TOP_VALUES_LIMIT = 50                   # top values stored for high-cardinality columns

MAX_TYPE_VIOLATION_SAMPLES = 5

# Common datetime patterns (regex, strptime format string).
# ORDER MATTERS: the first match wins. The 12-hour AM/PM pattern must come
# before the 24-hour "%m/%d/%Y %H:%M:%S" one, which has no end anchor and would
# otherwise claim "01/15/2020 12:00:00 AM" as 24-hour time.
_DATETIME_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%m/%d/%Y"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"), "%Y-%m-%dT%H:%M:%S"),
    (re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"), "%Y-%m-%d %H:%M:%S"),
    # US date + 12h time with AM/PM (e.g. "01/15/2020 12:00:00 AM")
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M$"), "%m/%d/%Y %I:%M:%S %p"),
    (re.compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}"), "%m/%d/%Y %H:%M:%S"),
]

# Sort keys for datetime min/max. Text order is not date order for US dates
# ("12/31/2023" sorts after "12/30/2024"), so the range is computed on a parsed
# (year, month, day, hour, minute, second) tuple and the original text of the
# winning value is kept for display. Timezone suffixes are ignored.
_ISO_KEY_RX = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?)?"
)
_US_KEY_RX = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})(?: (\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?(?: ([AP]M))?)?"
)


def _datetime_sort_key(value: str) -> Optional[tuple]:
    """Return a chronologically sortable tuple for a recognised datetime string."""
    m = _ISO_KEY_RX.match(value)
    if m:
        y, mo, d, h, mi, sec, frac = m.groups()
        return (int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(sec or 0),
                float("0." + frac) if frac else 0.0)
    m = _US_KEY_RX.match(value)
    if m:
        mo, d, y, h, mi, sec, frac, ampm = m.groups()
        hour = int(h or 0)
        if ampm:
            hour = hour % 12 + (12 if ampm == "PM" else 0)
        return (int(y), int(mo), int(d), hour, int(mi or 0), int(sec or 0),
                float("0." + frac) if frac else 0.0)
    return None


def _is_datetime_str(value: str) -> bool:
    for rx, _ in _DATETIME_PATTERNS:
        if rx.match(value):
            return True
    return False


def _get_datetime_format(value: str) -> Optional[str]:
    for rx, fmt in _DATETIME_PATTERNS:
        if rx.match(value):
            return fmt
    return None


def _classify_value(stripped: str) -> int:
    """Return type rank for a single value: 0 int, 1 float, 2 datetime, 3 string."""
    try:
        int(stripped)
        return 0
    except ValueError:
        pass
    try:
        float(stripped)
        return 1
    except ValueError:
        pass
    if _is_datetime_str(stripped):
        return 2
    return 3


@dataclass
class _ColAcc:
    """Per-column accumulator updated once per row."""
    name: str
    position: int
    # Type tracking (rank only advances upward)
    type_rank: int = 0
    # Per-rank observation counts (for type confidence — A7)
    rank_counts: list = field(default_factory=lambda: [0, 0, 0, 0])
    type_violations: list = field(default_factory=list)  # samples that didn't fit dominant type
    # Row counts
    count: int = 0        # non-null rows
    null_count: int = 0
    # Numeric stats — Welford + Neumaier (A1)
    num_count: int = 0
    num_mean: float = 0.0
    num_m2: float = 0.0          # Welford running sum of squared deltas
    num_sum: float = 0.0
    num_sum_compensation: float = 0.0  # Neumaier compensation
    num_min: float = field(default_factory=lambda: float("inf"))
    num_max: float = field(default_factory=lambda: float("-inf"))
    # Streaming quantile digest (A2)
    tdigest: Optional[TDigest] = None
    # Cardinality / value frequency
    value_counts: dict = field(default_factory=dict)
    cardinality_overflow: bool = False
    seen_duplicate: bool = False   # True the moment any value appears >1 time
    hll: Optional[HyperLogLog] = None  # populated after cardinality_overflow (A3)
    # Samples: first SAMPLE_SIZE distinct non-null values seen
    samples: list = field(default_factory=list)
    _samples_set: set = field(default_factory=set)
    # Datetime range (valid when type_rank == 2)
    dt_min: Optional[str] = None
    dt_max: Optional[str] = None
    dt_format: Optional[str] = None
    dt_min_key: Optional[tuple] = None
    dt_max_key: Optional[tuple] = None


def _welford_update(acc: _ColAcc, x: float) -> None:
    """Welford online mean + variance + Neumaier-compensated sum."""
    acc.num_count += 1
    delta = x - acc.num_mean
    acc.num_mean += delta / acc.num_count
    delta2 = x - acc.num_mean
    acc.num_m2 += delta * delta2
    # Neumaier-compensated sum
    t = acc.num_sum + x
    if abs(acc.num_sum) >= abs(x):
        acc.num_sum_compensation += (acc.num_sum - t) + x
    else:
        acc.num_sum_compensation += (x - t) + acc.num_sum
    acc.num_sum = t


def update_acc(acc: _ColAcc, raw_value: str) -> None:
    """Update accumulator with one raw string value from the CSV."""
    stripped = raw_value.strip() if raw_value else ""

    if stripped in _NULL_VALUES:
        acc.null_count += 1
        return

    acc.count += 1

    # Always classify the observed value to track type confidence (A7)
    observed_rank = _classify_value(stripped)
    acc.rank_counts[observed_rank] += 1

    # --- Fast path for finalized string columns ---
    if acc.type_rank == 3:
        if observed_rank != 3 and len(acc.type_violations) < MAX_TYPE_VIOLATION_SAMPLES:
            # already string, but record values that *would* have parsed differently
            pass  # string column accepts anything; no violation tracking needed
        vc = acc.value_counts
        if stripped in vc:
            vc[stripped] += 1
            acc.seen_duplicate = True
        elif not acc.cardinality_overflow:
            if len(vc) < MAX_CARDINALITY_TRACK:
                vc[stripped] = 1
            else:
                acc.cardinality_overflow = True
                if acc.hll is None:
                    acc.hll = HyperLogLog()
                    for k in vc:
                        acc.hll.add(k)
                acc.hll.add(stripped)
        elif acc.hll is not None:
            acc.hll.add(stripped)
        if len(acc.samples) < SAMPLE_SIZE and stripped not in acc._samples_set:
            acc.samples.append(stripped)
            acc._samples_set.add(stripped)
        return

    # --- Type promotion (rank only ratchets upward) ---
    if observed_rank > acc.type_rank:
        # record sample of values that broke prior assumption
        if len(acc.type_violations) < MAX_TYPE_VIOLATION_SAMPLES:
            acc.type_violations.append(stripped)
        acc.type_rank = observed_rank

    # --- Numeric stats (track if value parses numeric, regardless of column type) ---
    if observed_rank <= 1:
        try:
            num = float(stripped)
            if num < acc.num_min:
                acc.num_min = num
            if num > acc.num_max:
                acc.num_max = num
            _welford_update(acc, num)
            if acc.tdigest is None:
                acc.tdigest = TDigest(delta=TDIGEST_DELTA)
            acc.tdigest.add(num)
        except ValueError:
            pass

    # --- Datetime min/max ---
    if acc.type_rank == 2:
        if observed_rank == 2:
            key = _datetime_sort_key(stripped)
            if key is not None:
                if acc.dt_min_key is None or key < acc.dt_min_key:
                    acc.dt_min_key = key
                    acc.dt_min = stripped
                if acc.dt_max_key is None or key > acc.dt_max_key:
                    acc.dt_max_key = key
                    acc.dt_max = stripped
            if acc.dt_format is None:
                acc.dt_format = _get_datetime_format(stripped)

    # --- Cardinality / value counts ---
    vc = acc.value_counts
    if stripped in vc:
        vc[stripped] += 1
        acc.seen_duplicate = True
    elif not acc.cardinality_overflow:
        if len(vc) < MAX_CARDINALITY_TRACK:
            vc[stripped] = 1
        else:
            acc.cardinality_overflow = True
            if acc.hll is None:
                acc.hll = HyperLogLog()
                for k in vc:
                    acc.hll.add(k)
            acc.hll.add(stripped)
    elif acc.hll is not None:
        acc.hll.add(stripped)

    # --- Samples ---
    if len(acc.samples) < SAMPLE_SIZE and stripped not in acc._samples_set:
        acc.samples.append(stripped)
        acc._samples_set.add(stripped)


@dataclass
class ColumnProfile:
    """Fully computed profile for a single column."""
    name: str
    position: int
    type: str              # "integer", "float", "datetime", "string"
    count: int             # non-null row count
    null_count: int
    null_pct: float
    cardinality: int
    cardinality_is_exact: bool
    cardinality_estimated: bool
    cardinality_approx: Optional[int]
    is_unique: bool
    is_primary_key_candidate: bool
    min: Optional[Any]
    max: Optional[Any]
    mean: Optional[float]
    median: Optional[float]
    std_dev: Optional[float]
    variance: Optional[float]
    quantiles: Optional[dict]   # {"p01": ..., "p25": ..., "p50": ..., ...}
    sample_values: list
    value_index: Optional[dict]   # full {value: count} for cardinality <= 1000
    top_values: Optional[list]    # [{"value": ..., "count": ...}] for high-cardinality
    type_confidence: float
    type_violation_count: int
    type_violation_samples: list
    semantic_type: Optional[str] = None
    semantic_confidence: float = 0.0
    datetime_min: Optional[str] = None
    datetime_max: Optional[str] = None
    datetime_format: Optional[str] = None
    ai_summary: Optional[str] = None


def finalize_profile(acc: _ColAcc) -> ColumnProfile:
    """Build a ColumnProfile from a completed _ColAcc."""
    total = acc.count + acc.null_count
    null_pct = round(acc.null_count / total * 100, 1) if total > 0 else 0.0
    col_type = _TYPE_FROM_RANK[acc.type_rank]

    cardinality_exact = len(acc.value_counts)
    cardinality_estimated = acc.cardinality_overflow
    cardinality_approx: Optional[int] = None
    if cardinality_estimated and acc.hll is not None:
        cardinality_approx = acc.hll.estimate()
        cardinality = cardinality_approx
    else:
        cardinality = cardinality_exact

    is_unique = (not acc.seen_duplicate and acc.null_count == 0 and acc.count > 0)
    is_pk_candidate = (
        is_unique
        and col_type in ("integer", "string")
    )

    # Numeric stats — derived from Welford + Neumaier
    if col_type in ("integer", "float") and acc.num_count > 0:
        raw_min = acc.num_min
        raw_max = acc.num_max
        # Compensated sum gives a more accurate mean for huge or mixed-magnitude data
        compensated_sum = acc.num_sum + acc.num_sum_compensation
        mean_val: Optional[float] = round(compensated_sum / acc.num_count, 6)
        if acc.num_count >= 2:
            variance = acc.num_m2 / (acc.num_count - 1)
            std_dev: Optional[float] = round(variance ** 0.5, 6)
            variance_val: Optional[float] = round(variance, 6)
        else:
            std_dev = None
            variance_val = None

        quantiles: Optional[dict] = None
        median_val: Optional[float] = None
        if acc.tdigest is not None:
            q01 = acc.tdigest.quantile(0.01)
            q25 = acc.tdigest.quantile(0.25)
            q50 = acc.tdigest.quantile(0.5)
            q75 = acc.tdigest.quantile(0.75)
            q95 = acc.tdigest.quantile(0.95)
            q99 = acc.tdigest.quantile(0.99)
            if q50 is not None:
                if col_type == "integer":
                    median_val = round(q50, 1)
                else:
                    median_val = round(q50, 6)
                quantiles = {
                    "p01": round(q01, 6) if q01 is not None else None,
                    "p25": round(q25, 6) if q25 is not None else None,
                    "p50": round(q50, 6),
                    "p75": round(q75, 6) if q75 is not None else None,
                    "p95": round(q95, 6) if q95 is not None else None,
                    "p99": round(q99, 6) if q99 is not None else None,
                }
        if col_type == "integer":
            min_val = int(raw_min) if raw_min != float("inf") else None
            max_val = int(raw_max) if raw_max != float("-inf") else None
        else:
            min_val = raw_min if raw_min != float("inf") else None
            max_val = raw_max if raw_max != float("-inf") else None
    else:
        min_val = max_val = mean_val = median_val = None
        std_dev = variance_val = None
        quantiles = None

    # Convert sample values to their native type
    samples: list = []
    for s in acc.samples:
        if col_type == "integer":
            try:
                samples.append(int(s))
                continue
            except ValueError:
                pass
        elif col_type == "float":
            try:
                samples.append(float(s))
                continue
            except ValueError:
                pass
        samples.append(s)

    # Value index / top values
    if cardinality_exact <= VALUE_INDEX_CARDINALITY_LIMIT and not cardinality_estimated:
        value_index: Optional[dict] = {}
        for val_str, cnt in acc.value_counts.items():
            if col_type == "integer":
                try:
                    key: Any = int(val_str)
                except ValueError:
                    key = val_str
            elif col_type == "float":
                try:
                    key = float(val_str)
                except ValueError:
                    key = val_str
            else:
                key = val_str
            value_index[str(key)] = cnt
        top_values = None
    else:
        value_index = None
        sorted_vals = sorted(acc.value_counts.items(), key=lambda x: x[1], reverse=True)
        top_values = []
        for v, c in sorted_vals[:TOP_VALUES_LIMIT]:
            if col_type == "integer":
                try:
                    tv: Any = int(v)
                except (ValueError, OverflowError):
                    tv = v
            elif col_type == "float":
                try:
                    tv = float(v)
                except ValueError:
                    tv = v
            else:
                tv = v
            top_values.append({"value": tv, "count": c})

    # Type confidence (A7): fraction of non-null observations matching dominant type.
    # 'string' is a catchall — we measure confidence in primitive subtypes too:
    # for string columns confidence = 1.0 (everything is acceptable as string).
    if acc.count > 0:
        if col_type == "string":
            # confidence reflects "this really is text-only", not "everything fits".
            text_count = acc.rank_counts[3]
            type_confidence = round(text_count / acc.count, 3)
        else:
            dominant = acc.rank_counts[acc.type_rank]
            type_confidence = round(dominant / acc.count, 3)
    else:
        type_confidence = 0.0

    type_violation_count = sum(
        cnt for rank, cnt in enumerate(acc.rank_counts)
        if rank != acc.type_rank
    )

    # Semantic type detection (A6)
    semantic_type, semantic_confidence = detect_semantic_type(
        primitive_type=col_type,
        samples=acc.samples,
        column_name=acc.name,
    )

    return ColumnProfile(
        name=acc.name,
        position=acc.position,
        type=col_type,
        count=acc.count,
        null_count=acc.null_count,
        null_pct=null_pct,
        cardinality=cardinality,
        cardinality_is_exact=not cardinality_estimated,
        cardinality_estimated=cardinality_estimated,
        cardinality_approx=cardinality_approx,
        is_unique=is_unique,
        is_primary_key_candidate=is_pk_candidate,
        min=min_val,
        max=max_val,
        mean=mean_val,
        median=median_val,
        std_dev=std_dev,
        variance=variance_val,
        quantiles=quantiles,
        sample_values=samples,
        value_index=value_index,
        top_values=top_values,
        type_confidence=type_confidence,
        type_violation_count=type_violation_count,
        type_violation_samples=list(acc.type_violations),
        semantic_type=semantic_type,
        semantic_confidence=semantic_confidence,
        datetime_min=acc.dt_min if col_type == "datetime" else None,
        datetime_max=acc.dt_max if col_type == "datetime" else None,
        datetime_format=acc.dt_format if col_type == "datetime" else None,
    )


def infer_types_from_sample(
    accs: list,  # list[_ColAcc]
    sample_rows: list,
) -> list:
    """Run a subset of rows through the accumulators to detect preliminary types.

    Used to determine column types before creating the SQLite schema.
    Returns the modified accs list (same objects, updated in-place).
    """
    for row in sample_rows:
        for i, acc in enumerate(accs):
            raw = row[i] if i < len(row) else ""
            update_acc(acc, raw)
    return accs

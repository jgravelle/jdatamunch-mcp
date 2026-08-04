"""DataIndex dataclass and DataStore: save/load/list/delete dataset indexes.

Storage layout:
    ~/.data-index/
        {dataset_id}/
            index.json          — DataIndex: profiles, stats, schema
            index.json.sha256   — checksum of index.json (atomic-write sidecar)
            data.sqlite         — Row-level data for filtered retrieval
            _lock               — present while indexing; absence on read = consistent state
            _history.jsonl      — append-only profile snapshots (rotated to last 50)
        _savings.json           — Token savings tracker

Crash-safety guarantees (A4):
  * data.sqlite is written to data.sqlite.tmp first, then renamed.
  * index.json is written via tmp+rename plus a sidecar SHA-256.
  * _lock file marks an in-progress index_local; readers detect stale locks.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

INDEX_VERSION = 3  # v3 (1.6.0): runtime ingest tables (runtime_query_calls + runtime_redaction_log) materialised on first load


@dataclass
class DataIndex:
    """Index for a single tabular dataset."""
    dataset: str
    source_path: str
    source_format: str       # "csv", "xlsx", etc.
    source_hash: str         # SHA-256 of source file
    source_size_bytes: int
    indexed_at: str          # ISO datetime string
    index_version: int
    row_count: int
    column_count: int
    encoding: str
    delimiter: str
    columns: list            # list of column profile dicts (serialized ColumnProfile)
    sqlite_relative_path: str = "data.sqlite"
    dataset_summary: Optional[str] = None
    fingerprint: Optional[str] = None  # C2: content fingerprint independent of filename/path
    learned_null_tokens: list = field(default_factory=list)  # C3: detected sentinel-like tokens
    coverage: Optional[dict] = None  # 1.20.0: ingest coverage (walk, rows_indexed, skip_counts, recorded_at)


def _hash_file(path: str) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _profile_to_dict(p: Any) -> dict:
    """Serialize a ColumnProfile dataclass to a JSON-safe dict."""
    return {
        "name": p.name,
        "position": p.position,
        "type": p.type,
        "count": p.count,
        "null_count": p.null_count,
        "null_pct": p.null_pct,
        "cardinality": p.cardinality,
        "cardinality_is_exact": p.cardinality_is_exact,
        "cardinality_estimated": getattr(p, "cardinality_estimated", False),
        "cardinality_approx": getattr(p, "cardinality_approx", None),
        "is_unique": p.is_unique,
        "is_primary_key_candidate": p.is_primary_key_candidate,
        "min": p.min,
        "max": p.max,
        "mean": p.mean,
        "median": p.median,
        "std_dev": getattr(p, "std_dev", None),
        "variance": getattr(p, "variance", None),
        "quantiles": getattr(p, "quantiles", None),
        "sample_values": p.sample_values,
        "value_index": p.value_index,
        "top_values": p.top_values,
        "type_confidence": getattr(p, "type_confidence", 1.0),
        "type_violation_count": getattr(p, "type_violation_count", 0),
        "type_violation_samples": getattr(p, "type_violation_samples", []),
        "semantic_type": getattr(p, "semantic_type", None),
        "semantic_confidence": getattr(p, "semantic_confidence", 0.0),
        "datetime_min": p.datetime_min,
        "datetime_max": p.datetime_max,
        "datetime_format": p.datetime_format,
        "ai_summary": p.ai_summary,
    }


def _index_to_dict(idx: DataIndex) -> dict:
    return {
        "dataset": idx.dataset,
        "source_path": idx.source_path,
        "source_format": idx.source_format,
        "source_hash": idx.source_hash,
        "source_size_bytes": idx.source_size_bytes,
        "indexed_at": idx.indexed_at,
        "index_version": idx.index_version,
        "row_count": idx.row_count,
        "column_count": idx.column_count,
        "encoding": idx.encoding,
        "delimiter": idx.delimiter,
        "columns": idx.columns,  # already dicts
        "sqlite_relative_path": idx.sqlite_relative_path,
        "dataset_summary": idx.dataset_summary,
        "fingerprint": idx.fingerprint,
        "learned_null_tokens": idx.learned_null_tokens,
        "coverage": idx.coverage,
    }


def _dataset_mtime_ns(index_path, sqlite_path) -> int:
    """Newest mtime across the files a scan actually reads.

    Covers ``data.sqlite`` (and its WAL) as well as ``index.json``: the rows a
    search scans live in the SQLite store, so a reindex that rewrites rows must
    register even when the metadata monolith is untouched.
    """
    newest = 0
    for p in (index_path, sqlite_path, str(sqlite_path) + "-wal"):
        try:
            from pathlib import Path

            newest = max(newest, Path(p).stat().st_mtime_ns)
        except OSError:
            continue
    return newest


def _stamp_load_provenance(index, index_path, sqlite_path):
    """Record the dataset's on-disk mtime at load time.

    Backs the fifth absence refusal rule: a zero-result scan proves nothing if
    the dataset was rewritten while it was being read. Deliberately a
    filesystem signal so it still fires when a separate process drives the
    reindex.
    """
    try:
        index._dataset_paths = (str(index_path), str(sqlite_path))
        index._loaded_mtime_ns = _dataset_mtime_ns(index_path, sqlite_path)
    except Exception:  # pragma: no cover - defensive; never break a load
        import logging

        logging.getLogger(__name__).debug(
            "Could not stamp load provenance", exc_info=True
        )
    return index


def index_changed_since_load(index) -> bool:
    """Whether the dataset was rewritten since it was loaded (never raises).

    jData models no index freshness, which is why there is no stale-index
    refusal rule — but "was this rewritten under me" is a filesystem fact, not
    a freshness model, so unlike the stale gate this one is genuinely
    enforceable here rather than merely disclosed.

    Unknown is NOT changed: an index with no stamped provenance returns False
    rather than degrading every verdict.
    """
    try:
        paths = getattr(index, "_dataset_paths", None)
        loaded_at = getattr(index, "_loaded_mtime_ns", None)
        if not paths or loaded_at is None:
            return False
        current = _dataset_mtime_ns(paths[0], paths[1])
        if current == 0:
            # Nothing readable on disk: unknown, which is NOT changed. Reporting
            # a rewrite here would degrade every verdict against a dataset whose
            # files moved out from under the process.
            return False
        return current != int(loaded_at)
    except Exception:
        return False


def _index_from_dict(d: dict) -> DataIndex:
    return DataIndex(
        dataset=d["dataset"],
        source_path=d["source_path"],
        source_format=d["source_format"],
        source_hash=d["source_hash"],
        source_size_bytes=d["source_size_bytes"],
        indexed_at=d["indexed_at"],
        index_version=d.get("index_version", 1),
        row_count=d["row_count"],
        column_count=d["column_count"],
        encoding=d.get("encoding", "utf-8"),
        delimiter=d.get("delimiter", ","),
        columns=d.get("columns", []),
        sqlite_relative_path=d.get("sqlite_relative_path", "data.sqlite"),
        dataset_summary=d.get("dataset_summary"),
        fingerprint=d.get("fingerprint"),
        learned_null_tokens=d.get("learned_null_tokens", []),
        coverage=d.get("coverage"),
    )


class DataStore:
    """Storage for dataset indexes with helpers for all CRUD operations."""

    def __init__(self, base_path: Optional[str] = None):
        if base_path:
            self.base_path = Path(base_path)
        else:
            self.base_path = Path.home() / ".data-index"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def dataset_dir(self, dataset_id: str) -> Path:
        return self.base_path / dataset_id

    def index_path(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "index.json"

    def sqlite_path(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "data.sqlite"

    def lock_path(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "_lock"

    def history_path(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "_history.jsonl"

    def index_checksum_path(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "index.json.sha256"

    # ------------------------------------------------------------------
    # Lock / crash-safety helpers (A4)
    # ------------------------------------------------------------------

    def acquire_lock(self, dataset_id: str) -> None:
        """Mark a dataset as 'index in progress'. Caller should call release_lock on success."""
        d = self.dataset_dir(dataset_id)
        d.mkdir(parents=True, exist_ok=True)
        lock = self.lock_path(dataset_id)
        lock.write_text(datetime.now().isoformat(), encoding="utf-8")

    def release_lock(self, dataset_id: str) -> None:
        lock = self.lock_path(dataset_id)
        if lock.exists():
            try:
                lock.unlink()
            except OSError:
                pass

    def cleanup_stale_artifacts(self, dataset_id: str) -> bool:
        """Remove tmp SQLite + leftover lock if a prior index_local crashed.

        Returns True if cleanup was performed (caller should treat dataset as not indexed).
        """
        d = self.dataset_dir(dataset_id)
        if not d.exists():
            return False
        had_lock = self.lock_path(dataset_id).exists()
        had_tmp = (d / "data.sqlite.tmp").exists()
        had_index = self.index_path(dataset_id).exists()
        if had_lock and not had_index:
            # crash mid-load → clean everything tmp-ish, leave the dataset dir
            for stale in (d / "data.sqlite.tmp", d / "index.json.tmp", self.lock_path(dataset_id)):
                if stale.exists():
                    try:
                        stale.unlink()
                    except OSError:
                        pass
            return True
        if had_tmp and had_index:
            # successful index, but a stray tmp survived — delete it
            try:
                (d / "data.sqlite.tmp").unlink()
            except OSError:
                pass
        return False

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def save(
        self,
        dataset_id: str,
        profiles: list,   # list[ColumnProfile]
        source_path: str,
        source_format: str,
        row_count: int,
        encoding: str,
        delimiter: str,
        dataset_summary: Optional[str] = None,
        fingerprint: Optional[str] = None,
        learned_null_tokens: Optional[list] = None,
        coverage: Optional[dict] = None,
    ) -> DataIndex:
        """Build and persist a DataIndex from profiling results."""
        source_hash = _hash_file(source_path)
        source_size = Path(source_path).stat().st_size

        column_dicts = [_profile_to_dict(p) for p in profiles]

        idx = DataIndex(
            dataset=dataset_id,
            source_path=str(source_path),
            source_format=source_format,
            source_hash=source_hash,
            source_size_bytes=source_size,
            indexed_at=datetime.now().isoformat(),
            index_version=INDEX_VERSION,
            row_count=row_count,
            column_count=len(profiles),
            encoding=encoding,
            delimiter=delimiter,
            columns=column_dicts,
            dataset_summary=dataset_summary,
            fingerprint=fingerprint,
            learned_null_tokens=learned_null_tokens or [],
            coverage=coverage,
        )

        dir_ = self.dataset_dir(dataset_id)
        dir_.mkdir(parents=True, exist_ok=True)
        path = self.index_path(dataset_id)
        tmp = path.with_suffix(".json.tmp")
        payload = json.dumps(_index_to_dict(idx), indent=2).encode("utf-8")
        with open(tmp, "wb") as f:
            f.write(payload)
        tmp.replace(path)
        # Sidecar checksum (A4)
        checksum_path = self.index_checksum_path(dataset_id)
        checksum_path.write_text(_hash_bytes(payload), encoding="utf-8")

        return idx

    def append_history(self, dataset_id: str, snapshot: dict) -> None:
        """Append a profile snapshot to _history.jsonl, rotating to last 50 (A8)."""
        path = self.history_path(dataset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            existing.append(line)
            except OSError:
                existing = []
        existing.append(json.dumps(snapshot, separators=(",", ":")))
        # Keep last 50
        existing = existing[-50:]
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for line in existing:
                f.write(line + "\n")
        tmp.replace(path)

    def read_history(self, dataset_id: str, n: int = 10) -> list:
        path = self.history_path(dataset_id)
        if not path.exists():
            return []
        snaps: list = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    snaps.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return snaps[-n:] if n > 0 else snaps

    def load(self, dataset_id: str) -> Optional[DataIndex]:
        """Load a DataIndex from storage. Returns None if not found.

        Auto-runs migrations if index_version < current INDEX_VERSION (A11).
        """
        path = self.index_path(dataset_id)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                raw = f.read()
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None

        # Run migrations to bring the on-disk dict up to current version (A11)
        from .migrations import migrate_to_current
        try:
            data = migrate_to_current(data)
        except Exception:
            return None

        if data.get("index_version", 1) != INDEX_VERSION:
            return None  # version mismatch we can't migrate → triggers full re-index

        index = _index_from_dict(data)
        return _stamp_load_provenance(index, path, self.sqlite_path(dataset_id))

    def source_freshness(self, index) -> dict:
        """Cheap, PROVABLE-ONLY freshness reading for a dataset's source file.

        ⚠ This never returns ``fresh``, and that is the point, not a gap.
        jData's standing product call (2026-07-24) is that a permanent
        ``index: "fresh"`` asserts currency this product cannot back, so the
        index channel appears only as a positive detection. This keeps that
        rule exactly: it reports what it can PROVE and says ``unknown``
        otherwise.

        What it can prove cheaply, with a ``stat`` and no hashing:

        * ``missing_source`` — the file we indexed is gone. Whatever we serve
          describes something that is no longer there.
        * ``stale`` — the size differs from the size recorded at index time, so
          the content definitely changed.
        * ``unknown`` — the size matches, or we could not look. A matching size
          does NOT prove matching content, and ``verify_source`` exists for
          callers who want that answer and will pay for it.

        Deliberately not hashing: the source can be hundreds of megabytes and
        this runs on ordinary read calls. ``needs_reindex`` already does the
        full hash comparison where paying for it is the point.
        """
        source_path = getattr(index, "source_path", "") or ""
        if not source_path:
            return {"state": "unknown", "reason": "no source path recorded"}
        try:
            p = Path(source_path)
            if not p.exists():
                return {"state": "missing_source", "source_path": source_path}
            size = p.stat().st_size
        except OSError as exc:
            return {"state": "unknown", "reason": f"source unreadable: {exc}"}

        recorded = getattr(index, "source_size_bytes", None)
        if recorded is not None and size != recorded:
            return {
                "state": "stale",
                "source_path": source_path,
                "indexed_size_bytes": recorded,
                "current_size_bytes": size,
            }
        return {
            "state": "unknown",
            "reason": (
                "size matches the indexed size, which does not prove the "
                "content matches. jData does not track index freshness; pass "
                "verify_source=True to compare content hashes."
            ),
        }

    def verify_source(self, index) -> dict:
        """The EXPENSIVE reading: compare the source file's content hash.

        Only this can return ``fresh``, because only this actually establishes
        it. Opt-in, because it re-hashes the whole source file.
        """
        source_path = getattr(index, "source_path", "") or ""
        if not source_path or not Path(source_path).exists():
            return self.source_freshness(index)
        try:
            current = _hash_file(source_path)
        except OSError as exc:
            return {"state": "unknown", "reason": f"source unreadable: {exc}"}
        recorded = getattr(index, "source_hash", "") or ""
        if not recorded:
            return {"state": "unknown", "reason": "no source hash recorded"}
        if current == recorded:
            return {"state": "fresh", "verified": "content_hash"}
        return {"state": "stale", "verified": "content_hash", "source_path": source_path}

    def needs_reindex(self, dataset_id: str, source_path: str) -> bool:
        """Return True if the source file has changed or was never indexed."""
        idx = self.load(dataset_id)
        if idx is None:
            return True
        current_hash = _hash_file(source_path)
        return idx.source_hash != current_hash

    def list_datasets(self) -> list:
        """Return summary info for all indexed datasets."""
        result = []
        for subdir in sorted(self.base_path.iterdir()):
            if not subdir.is_dir() or subdir.name.startswith("_"):
                continue
            index_file = subdir / "index.json"
            if not index_file.exists():
                continue
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    d = json.load(f)
                result.append({
                    "dataset": d["dataset"],
                    "file": Path(d["source_path"]).name,
                    "rows": d["row_count"],
                    "columns": d["column_count"],
                    "size_bytes": d["source_size_bytes"],
                    "source_format": d.get("source_format", "csv"),
                    "fingerprint": d.get("fingerprint"),
                    "indexed_at": d["indexed_at"],
                })
            except Exception:
                continue
        return result

    def delete(self, dataset_id: str) -> bool:
        """Delete a dataset index and its SQLite file."""
        dir_ = self.dataset_dir(dataset_id)
        if not dir_.exists():
            return False
        shutil.rmtree(dir_)
        return True

"""list_repos tool: List GitHub repositories indexed via index_repo."""

import time
from pathlib import Path
from typing import Optional

from ..config import get_index_path
from ..storage.data_store import DataStore


def list_repos(storage_path: Optional[str] = None) -> dict:
    """List all GitHub repositories that have been indexed via index_repo.

    Scans for .repo-sha-* marker files and aggregates dataset info per repo.
    """
    t0 = time.time()
    base = Path(storage_path or str(get_index_path()))

    if not base.exists():
        return {
            "result": [],
            "_meta": {"timing_ms": round((time.time() - t0) * 1000, 1)},
        }

    # Find all repo SHA marker files
    markers = sorted(base.glob(".repo-sha-*"))
    if not markers:
        return {
            "result": [],
            "_meta": {"timing_ms": round((time.time() - t0) * 1000, 1)},
        }

    store = DataStore(base_path=str(base))
    all_datasets = store.list_datasets()

    repos: list[dict] = []
    for marker in markers:
        # .repo-sha-{owner}--{repo} → owner--repo
        prefix = marker.name.removeprefix(".repo-sha-")
        head_sha = marker.read_text(encoding="utf-8", errors="replace").strip()

        # Reconstruct owner/repo from prefix
        parts = prefix.split("--", 1)
        if len(parts) == 2:
            repo_display = f"{parts[0]}/{parts[1]}"
        else:
            repo_display = prefix

        # Find datasets belonging to this repo
        repo_datasets = [
            d for d in all_datasets
            if d["dataset"].startswith(prefix + "--")
        ]

        total_rows = sum(d["rows"] for d in repo_datasets)
        total_size = sum(d["size_bytes"] for d in repo_datasets)

        repos.append({
            "repo": repo_display,
            "head_sha": head_sha[:12] if head_sha else None,
            "datasets": len(repo_datasets),
            "total_rows": total_rows,
            "total_size_bytes": total_size,
            "dataset_names": [d["dataset"] for d in repo_datasets],
        })

    return {
        "result": repos,
        "_meta": {"timing_ms": round((time.time() - t0) * 1000, 1)},
    }

"""index_repo tool: Index data files from a GitHub repository."""

import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from ..config import get_index_path, get_max_rows
from .index_local import index_local

logger = logging.getLogger(__name__)

# Supported data file extensions
DATA_EXTENSIONS = frozenset([
    ".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".jsonl", ".ndjson",
])

# Limits
MAX_FILE_SIZE = 50 * 1024 * 1024   # 50 MB per file
MAX_FILES = 20                      # max data files to index per repo

# Paths to skip
SKIP_PATTERNS = frozenset([
    "node_modules/", ".git/", "__pycache__/", ".venv/", "venv/",
    ".tox/", ".pytest_cache/", "dist/", "build/", ".eggs/",
])


def parse_github_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from GitHub URL or owner/repo string."""
    url = url.removesuffix(".git")
    if "/" in url and "://" not in url:
        parts = url.split("/")
        return parts[0], parts[1]
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    raise ValueError(f"Could not parse GitHub URL: {url}")


def _should_skip(path: str) -> bool:
    """Check if a file path should be skipped."""
    normalized = path.replace("\\", "/")
    for pat in SKIP_PATTERNS:
        if pat in normalized:
            return True
    return False


async def _fetch_head_sha(
    owner: str, repo: str, token: Optional[str],
    client: httpx.AsyncClient,
) -> Optional[str]:
    """Fetch HEAD commit SHA (single lightweight request)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json().get("sha")
    except Exception:
        return None


async def _fetch_repo_tree(
    owner: str, repo: str, token: Optional[str],
    client: httpx.AsyncClient,
) -> list[dict]:
    """Fetch the full recursive tree for HEAD."""
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    r = await client.get(url, params={"recursive": "1"}, headers=headers)
    r.raise_for_status()
    return r.json().get("tree", [])


def _discover_data_files(tree: list[dict]) -> tuple[list[dict], dict]:
    """Filter tree entries to supported data files within size limits.

    Returns (files, skip_counts) — every file excluded at discovery is
    tallied by reason so the coverage block (1.20.0) can disclose it.
    """
    files = []
    skip_counts: dict = {}

    def _skip(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    for entry in tree:
        if entry.get("type") != "blob":
            continue
        path = entry.get("path", "")
        size = entry.get("size", 0)

        ext = os.path.splitext(path)[1].lower()
        if ext not in DATA_EXTENSIONS:
            _skip("unsupported_extension")
            continue
        if _should_skip(path):
            _skip("skipped_path")
            continue
        if size > MAX_FILE_SIZE:
            _skip("oversize")
            continue

        files.append({"path": path, "size": size})

    # Sort by size ascending (index smaller files first)
    files.sort(key=lambda f: f["size"])
    if len(files) > MAX_FILES:
        skip_counts["over_file_limit"] = len(files) - MAX_FILES
    return files[:MAX_FILES], skip_counts


async def _download_file(
    owner: str, repo: str, path: str, dest: str,
    token: Optional[str], client: httpx.AsyncClient,
) -> bool:
    """Download a raw file from GitHub to a local path."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {"Accept": "application/vnd.github.v3.raw"}
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        return True
    except Exception:
        return False


async def index_repo(
    url: str,
    incremental: bool = True,
    github_token: Optional[str] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Index data files from a GitHub repository.

    Pipeline:
    1. Parse GitHub URL → owner/repo
    2. Fetch HEAD SHA (incremental fast-path if unchanged)
    3. Fetch repo tree, discover data files
    4. Download each file to temp dir
    5. Index each via existing index_local pipeline
    6. Return summary
    """
    t0 = time.time()
    store_path = storage_path or str(get_index_path())

    try:
        owner, repo = parse_github_url(url)
    except ValueError as e:
        return {"error": str(e)}

    if not github_token:
        github_token = os.environ.get("GITHUB_TOKEN")

    repo_prefix = f"{owner}--{repo}"
    warnings: list[str] = []
    indexed_datasets: list[dict] = []
    skipped: list[dict] = []

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Fetch HEAD SHA
            head_sha = await _fetch_head_sha(owner, repo, github_token, client)

            # Incremental: check if we have a marker file with the same SHA
            marker_path = Path(store_path) / f".repo-sha-{repo_prefix}"
            if incremental and head_sha and marker_path.exists():
                stored_sha = marker_path.read_text(encoding="utf-8", errors="replace").strip()
                if stored_sha == head_sha:
                    return {
                        "result": {
                            "repo": f"{owner}/{repo}",
                            "skipped": True,
                            "reason": "HEAD SHA unchanged (incremental=true)",
                            "head_sha": head_sha,
                        },
                        "_meta": {"timing_ms": round((time.time() - t0) * 1000, 1)},
                    }

            # Fetch tree
            try:
                tree = await _fetch_repo_tree(owner, repo, github_token, client)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return {"error": f"Repository not found: {owner}/{repo}"}
                if e.response.status_code == 403:
                    return {"error": "GitHub API rate limit exceeded. Set GITHUB_TOKEN env var."}
                return {"error": f"GitHub API error: {e.response.status_code}"}

            data_files, skip_counts = _discover_data_files(tree)
            if not data_files:
                return {"error": f"No data files found in {owner}/{repo}. Supported: {', '.join(sorted(DATA_EXTENSIONS))}"}

            # Download and index each file
            semaphore = asyncio.Semaphore(5)

            async def download_one(entry: dict) -> Optional[tuple[str, str]]:
                """Download a file, return (repo_path, local_path) or None."""
                async with semaphore:
                    fd, local_path = tempfile.mkstemp(
                        suffix=os.path.splitext(entry["path"])[1]
                    )
                    os.close(fd)
                    ok = await _download_file(
                        owner, repo, entry["path"], local_path,
                        github_token, client,
                    )
                    if ok:
                        return entry["path"], local_path
                    else:
                        os.unlink(local_path)
                        warnings.append(f"Failed to download {entry['path']}")
                        skip_counts["download_failed"] = skip_counts.get("download_failed", 0) + 1
                        return None

            download_tasks = [download_one(e) for e in data_files]
            results = await asyncio.gather(*download_tasks)

        # Index each downloaded file (outside the HTTP client context)
        for result in results:
            if result is None:
                continue
            repo_path, local_path = result
            try:
                # Dataset ID: owner--repo--filename_stem
                stem = Path(repo_path).stem.lower().replace(" ", "-")
                dataset_id = f"{repo_prefix}--{stem}"

                idx_result = index_local(
                    path=local_path,
                    name=dataset_id,
                    incremental=incremental,
                    storage_path=store_path,
                )

                if "error" in idx_result:
                    warnings.append(f"{repo_path}: {idx_result['error']}")
                    skip_counts["index_error"] = skip_counts.get("index_error", 0) + 1
                elif idx_result.get("result", {}).get("skipped"):
                    skipped.append({
                        "file": repo_path,
                        "dataset": dataset_id,
                        "reason": "unchanged",
                    })
                else:
                    r = idx_result.get("result", {})
                    indexed_datasets.append({
                        "file": repo_path,
                        "dataset": dataset_id,
                        "rows": r.get("rows", 0),
                        "columns": r.get("columns", 0),
                    })
            except Exception as e:
                warnings.append(f"{repo_path}: {e}")
                skip_counts["index_error"] = skip_counts.get("index_error", 0) + 1
            finally:
                try:
                    os.unlink(local_path)
                except OSError:
                    pass

        # Save HEAD SHA marker for incremental
        if head_sha:
            Path(store_path).mkdir(parents=True, exist_ok=True)
            marker_path.write_text(head_sha, encoding="utf-8")

        # Coverage block (1.20.0): what this repo ingest excluded, by reason.
        # Persisted as a sidecar next to the .repo-sha marker so a later
        # session can still attest what the last ingest walked. A full
        # re-ingest overwrites it (self-heals).
        coverage: dict = {
            "walk": "full",
            "datasets_indexed": len(indexed_datasets),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        nonzero_skips = {k: v for k, v in skip_counts.items() if v}
        if nonzero_skips:
            coverage["skip_counts"] = nonzero_skips
        try:
            Path(store_path).mkdir(parents=True, exist_ok=True)
            (Path(store_path) / f".repo-coverage-{repo_prefix}.json").write_text(
                json.dumps(coverage), encoding="utf-8"
            )
        except OSError as exc:
            logger.debug("Could not persist repo coverage sidecar: %s", exc)

        duration_s = time.time() - t0
        result_body: dict = {
            "repo": f"{owner}/{repo}",
            "head_sha": head_sha,
            "data_files_found": len(data_files),
            "datasets_indexed": len(indexed_datasets),
            "datasets_skipped": len(skipped),
            "datasets": indexed_datasets,
            "coverage": coverage,
            "duration_seconds": round(duration_s, 1),
        }
        if skipped:
            result_body["skipped"] = skipped
        if warnings:
            result_body["warnings"] = warnings

        return {
            "result": result_body,
            "_meta": {"timing_ms": round(duration_s * 1000, 1)},
        }

    except Exception as e:
        return {"error": f"index_repo failed: {e}"}

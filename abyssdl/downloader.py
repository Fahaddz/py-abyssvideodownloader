from __future__ import annotations

import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .client import fetch_binary_response
from .constants import (
    AUTO_CONNECTION_CANDIDATES,
    DEFAULT_RETRIES,
    DEFAULT_RETUNE_THRESHOLD_MBPS,
    DEFAULT_TIMEOUT,
    FAST_CONNECTION_CANDIDATES,
    FRAGMENT_SIZE,
    PROBE_BYTES,
    PROBE_SEGMENTS_PER_CANDIDATE,
    PROBE_TIMEOUT,
    READ_CHUNK_SIZE,
)
from .models import DownloadPlan
from .pool import close_thread_conns, fetch_full_stream, fetch_range
from .segments import build_segment_url, expected_segment_size

ProgressCallback = Callable[[int, int], None]
StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class DownloadOptions:
    output: Path
    connections: int
    retries: int = DEFAULT_RETRIES
    timeout: int = DEFAULT_TIMEOUT
    headers: dict[str, str] | None = None
    overwrite: bool = False
    resume: bool = True
    keep_temp: bool = False
    status: StatusCallback | None = None
    retune_below_mbps: float = DEFAULT_RETUNE_THRESHOLD_MBPS
    thorough: bool = False


def sanitize_filename(value: str) -> str:
    cleaned = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in value)
    return cleaned.strip(" .") or "video"


def default_output_path(video_id: str, label: str | None, out_dir: Path) -> Path:
    name = sanitize_filename(f"{video_id}_{label or 'video'}.mp4")
    candidate = out_dir / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        next_candidate = out_dir / f"{stem}_{index}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        index += 1


def _temp_root(output: Path) -> Path:
    return output.parent / ".abyss-temp"


def temp_dir_for(plan: DownloadPlan, output: Path) -> Path:
    safe = sanitize_filename(f"{plan.video_id}_{plan.source.res_id}_{plan.total_size}")
    return _temp_root(output) / safe


def manifest_for(plan: DownloadPlan) -> dict:
    return {
        "video_id": plan.video_id,
        "res_id": plan.source.res_id,
        "size": plan.total_size,
        "fragment_size": FRAGMENT_SIZE,
        "segments": len(plan.tokens),
    }


def _write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _manifest_matches(path: Path, expected: dict) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")) == expected
    except Exception:
        return False


def prepare_temp_dir(plan: DownloadPlan, output: Path, resume: bool) -> Path:
    temp_dir = temp_dir_for(plan, output)
    manifest_path = temp_dir / "manifest.json"
    expected = manifest_for(plan)
    if temp_dir.exists() and (not resume or not _manifest_matches(manifest_path, expected)):
        shutil.rmtree(temp_dir)
    (temp_dir / "segments").mkdir(parents=True, exist_ok=True)
    _write_manifest(manifest_path, expected)
    return temp_dir


def valid_segment(path: Path, index: int, total_size: int) -> bool:
    return path.is_file() and path.stat().st_size == expected_segment_size(index, total_size)


def missing_segments(plan: DownloadPlan, temp_dir: Path) -> list[int]:
    segments_dir = temp_dir / "segments"
    missing = []
    for index in range(len(plan.tokens)):
        path = segments_dir / f"segment_{index}"
        if not valid_segment(path, index, plan.total_size):
            part = segments_dir / f"segment_{index}.part"
            if part.exists():
                part.unlink()
            missing.append(index)
    return missing


def _download_segment(
    plan: DownloadPlan,
    options: DownloadOptions,
    temp_dir: Path,
    index: int,
    progress: ProgressCallback | None = None,
) -> int:
    token = plan.tokens[index]
    url = build_segment_url(plan.base_url, plan.total_size, token)
    segments_dir = temp_dir / "segments"
    final_path = segments_dir / f"segment_{index}"
    part_path = segments_dir / f"segment_{index}.part"
    expected_size = expected_segment_size(index, plan.total_size)

    for attempt in range(options.retries + 1):
        written = 0
        try:
            if part_path.exists():
                part_path.unlink()
            # Keep-alive pooled fetch: reuses the thread's TLS connection.
            with part_path.open("wb") as out:
                def _on_chunk(data: bytes) -> None:
                    nonlocal written
                    out.write(data)
                    written += len(data)
                    if progress:
                        progress(len(data), 0)

                total = fetch_full_stream(
                    url,
                    options.headers,
                    options.timeout,
                    chunk_size=READ_CHUNK_SIZE,
                    on_chunk=_on_chunk,
                )
            if total != expected_size or part_path.stat().st_size != expected_size:
                raise IOError(f"segment {index} size mismatch ({total} != {expected_size})")
            os.replace(part_path, final_path)
            return expected_size
        except Exception:
            close_thread_conns()
            if progress and written:
                progress(-written, 0)
            if attempt >= options.retries:
                if part_path.exists():
                    part_path.unlink()
                raise
            time.sleep(min(2.0, 0.4 * (attempt + 1)))
    raise RuntimeError("unreachable")


def _probe_bytes(
    plan: DownloadPlan,
    options: DownloadOptions,
    index: int,
) -> int:
    """Download only the first PROBE_BYTES of a segment (Range request).

    Used for fast auto-tuning: measures CDN throughput without paying for
    full 2 MB segments or writing anything to disk. Returns bytes fetched.
    """
    token = plan.tokens[index % len(plan.tokens)]
    url = build_segment_url(plan.base_url, plan.total_size, token)
    size = min(PROBE_BYTES, expected_segment_size(index % len(plan.tokens), plan.total_size))
    try:
        body, status = fetch_range(
            url,
            options.headers,
            PROBE_TIMEOUT,
            start=0,
            end=size - 1,
        )
    except Exception:
        close_thread_conns()
        raise
    if status not in (200, 206) or not body:
        raise IOError(f"probe {index} failed: HTTP {status}")
    return len(body)


def _download_many(
    plan: DownloadPlan,
    options: DownloadOptions,
    temp_dir: Path,
    indexes: list[int],
    workers: int,
    progress: ProgressCallback | None = None,
    retry_stragglers: bool = True,
) -> int:
    if not indexes:
        return 0
    worker_count = max(1, min(workers, len(indexes)))
    total = 0
    failed: list[int] = []
    errors: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_download_segment, plan, options, temp_dir, index, progress): index
            for index in indexes
        }
        for future in as_completed(futures):
            try:
                total += future.result()
                if progress:
                    progress(0, 1)
            except Exception as exc:
                failed.append(futures[future])
                errors.append(exc)

    if failed and retry_stragglers:
        straggler_workers = min(4, len(failed))
        _status(
            options,
            f"Retrying {len(failed)} slow segment(s) with {straggler_workers} low-concurrency worker(s)...",
        )
        total += _download_many(
            plan,
            options,
            temp_dir,
            failed,
            straggler_workers,
            progress,
            retry_stragglers=False,
        )
    elif failed:
        raise errors[-1]
    return total


def _status(options: DownloadOptions, message: str) -> None:
    if options.status:
        options.status(message)


def _rate_mbps(bytes_done: int, elapsed: float) -> float:
    return bytes_done / max(elapsed, 0.001) / (1024 * 1024)


def _probe_many(
    plan: DownloadPlan,
    options: DownloadOptions,
    indexes: list[int],
    workers: int,
) -> int:
    """Fetch PROBE_BYTES from each index in parallel. Returns total bytes."""
    if not indexes:
        return 0
    worker_count = max(1, min(workers, len(indexes)))
    total = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_probe_bytes, plan, options, i): i for i in indexes}
        for future in as_completed(futures):
            total += future.result()
    return total


def bench_connections(
    plan: DownloadPlan,
    options: DownloadOptions,
    candidates: tuple[int, ...] | list[int] | None = None,
    probe_segments: int = PROBE_SEGMENTS_PER_CANDIDATE,
    status: StatusCallback | None = None,
    start_offset: int = 0,
) -> list[tuple[int, float]]:
    """Fast benchmark: Range-probe each candidate, return [(conns, MB/s)].

    Cost: len(candidates) * probe_segments * 1 MB (default 4x3 = 12 MB)
    instead of the old 192 full segments (~384 MB). Takes seconds, and
    downloads nothing to disk so it never pollutes resume state.
    """
    if candidates is None:
        candidates = FAST_CONNECTION_CANDIDATES if not options.thorough else AUTO_CONNECTION_CANDIDATES
    results: list[tuple[int, float]] = []
    cursor = start_offset
    for candidate in candidates:
        sample = list(range(cursor, cursor + probe_segments))
        cursor += probe_segments
        if status:
            status(f"Probing {candidate} connections ({probe_segments} x 1 MB samples)...")
        try:
            start = time.perf_counter()
            bytes_done = _probe_many(plan, options, sample, candidate)
            rate = _rate_mbps(bytes_done, time.perf_counter() - start)
        except Exception as exc:
            if status:
                status(f"{candidate} connections: failed ({exc})")
            results.append((candidate, 0.0))
            continue
        if status:
            status(f"{candidate} connections: {rate:.1f} MB/s")
        results.append((candidate, rate))
    return results


def _retune_connections(
    plan: DownloadPlan,
    options: DownloadOptions,
    temp_dir: Path,
    remaining: list[int],
    progress: ProgressCallback | None,
    heading: str,
) -> tuple[int, float]:
    # Thorough mode preserves the old behavior: download real segments per
    # candidate and keep them. Slow (~384 MB) but measures full writes.
    if options.thorough:
        best_connections = 16
        best_rate = 0.0
        _status(options, heading + " (thorough: full segments)")
        for candidate in AUTO_CONNECTION_CANDIDATES:
            if not remaining:
                break
            sample_count = min(candidate, len(remaining))
            sample = remaining[:sample_count]
            _status(options, f"Testing {candidate} connections on {sample_count} segments...")
            start = time.perf_counter()
            bytes_done = _download_many(plan, options, temp_dir, sample, candidate, progress)
            rate = _rate_mbps(bytes_done, time.perf_counter() - start)
            _status(options, f"{candidate} connections: {rate:.1f} MB/s")
            if rate > best_rate:
                best_rate = rate
                best_connections = candidate
            del remaining[:sample_count]
        return best_connections, best_rate

    # Fast path (default): 1 MB Range probes, ~12 MB total, nothing written.
    _status(options, heading + " (fast probe: ~12 MB, seconds)")
    results = bench_connections(plan, options, status=lambda m: _status(options, m))
    if not results:
        return 16, 0.0
    best_connections, best_rate = max(results, key=lambda r: r[1])
    if best_rate <= 0:
        best_connections, best_rate = 16, 0.0
    return best_connections, best_rate


def _download_adaptive_batches(
    plan: DownloadPlan,
    options: DownloadOptions,
    temp_dir: Path,
    remaining: list[int],
    connections: int,
    progress: ProgressCallback | None,
) -> None:
    current_connections = connections
    threshold = max(0.0, options.retune_below_mbps)
    # Track the tuned rate so slow-but-stable routes don't retune-loop:
    # only re-probe on a *significant* drop, not merely "below threshold".
    tuned_rate = 0.0
    # Peek at last probe result via a cheap single-probe? Instead, use the
    # first batch as the baseline when tuned_rate is unknown.
    first_batch = True

    while remaining:
        batch_size = min(len(remaining), max(16, min(current_connections * 2, 96)))
        batch = remaining[:batch_size]
        start = time.perf_counter()
        bytes_done = _download_many(plan, options, temp_dir, batch, current_connections, progress)
        rate = _rate_mbps(bytes_done, time.perf_counter() - start)
        del remaining[:batch_size]

        if first_batch:
            tuned_rate = rate
            first_batch = False
        else:
            tuned_rate = max(tuned_rate, rate)

        significant_drop = rate < 0.5 * tuned_rate if tuned_rate > 0 else False
        if (
            threshold > 0
            and rate < threshold
            and significant_drop
            and len(remaining) >= AUTO_CONNECTION_CANDIDATES[0]
        ):
            _status(
                options,
                f"Speed dropped to {rate:.1f} MB/s below {threshold:.1f} MB/s; re-tuning connections...",
            )
            current_connections, _ = _retune_connections(
                plan,
                options,
                temp_dir,
                remaining,
                progress,
                "Re-testing connection counts against the current CDN throttle state...",
            )
            if remaining:
                _status(options, f"Using {current_connections} connections for the next batch.")


def _download_missing_auto(
    plan: DownloadPlan,
    options: DownloadOptions,
    temp_dir: Path,
    missing: list[int],
    progress: ProgressCallback | None = None,
) -> None:
    remaining = list(missing)
    best_connections, _ = _retune_connections(
        plan,
        options,
        temp_dir,
        remaining,
        progress,
        "Auto-tuning connections against the current CDN route...",
    )

    if remaining:
        _status(options, f"Using {best_connections} connections for the remaining segments.")
        _download_adaptive_batches(plan, options, temp_dir, remaining, best_connections, progress)


def _recover_missing_segments(
    plan: DownloadPlan,
    options: DownloadOptions,
    temp_dir: Path,
    progress: ProgressCallback | None = None,
    last_error: BaseException | None = None,
) -> None:
    max_passes = max(6, min(12, options.retries))
    for attempt in range(1, max_passes + 1):
        missing = missing_segments(plan, temp_dir)
        if not missing:
            return

        workers = 4 if attempt <= 3 else 2 if attempt <= 6 else 1
        workers = min(workers, len(missing))
        _status(
            options,
            f"Recovery pass {attempt}/{max_passes}: retrying {len(missing)} missing segment(s) with {workers} worker(s)...",
        )
        try:
            _download_many(
                plan,
                options,
                temp_dir,
                missing,
                workers,
                progress,
                retry_stragglers=False,
            )
        except Exception as exc:
            last_error = exc

        if not missing_segments(plan, temp_dir):
            return
        time.sleep(min(30, 2 + attempt * 2))

    still_missing = missing_segments(plan, temp_dir)
    if still_missing:
        details = f" Last error: {last_error}" if last_error else ""
        raise RuntimeError(
            f"Could not finish {len(still_missing)} segment(s) after repeated recovery attempts.{details}"
        )


def merge_segments(plan: DownloadPlan, temp_dir: Path, output: Path, overwrite: bool) -> None:
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    part_output = output.with_name(output.name + ".part")
    if part_output.exists():
        part_output.unlink()
    with part_output.open("wb") as merged:
        for index in range(len(plan.tokens)):
            segment = temp_dir / "segments" / f"segment_{index}"
            if not valid_segment(segment, index, plan.total_size):
                raise IOError(f"Segment {index} is missing or incomplete")
            with segment.open("rb") as src:
                shutil.copyfileobj(src, merged, length=1024 * 1024)
    if output.exists() and overwrite:
        output.unlink()
    os.replace(part_output, output)


def download_plan(
    plan: DownloadPlan,
    options: DownloadOptions,
    progress: ProgressCallback | None = None,
) -> Path:
    output = options.output
    if output.exists() and not options.overwrite:
        raise FileExistsError(f"Output already exists: {output}")

    temp_dir = prepare_temp_dir(plan, output, options.resume)
    missing = missing_segments(plan, temp_dir)
    completed_bytes = plan.total_size - sum(expected_segment_size(i, plan.total_size) for i in missing)
    if progress and completed_bytes > 0:
        progress(completed_bytes, 0)

    if missing:
        last_error = None
        try:
            if options.connections <= 0:
                _download_missing_auto(plan, options, temp_dir, missing, progress)
            else:
                _download_many(plan, options, temp_dir, missing, options.connections, progress)
        except Exception as exc:
            last_error = exc
            _status(options, f"Download pass hit a network error: {exc}")

        if missing_segments(plan, temp_dir):
            _recover_missing_segments(plan, options, temp_dir, progress, last_error)

    merge_segments(plan, temp_dir, output, options.overwrite)
    if not options.keep_temp:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return output

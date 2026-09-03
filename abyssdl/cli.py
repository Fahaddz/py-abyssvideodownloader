from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Prompt
from rich.table import Table

from .constants import (
    DEFAULT_CONNECTIONS,
    DEFAULT_RETRIES,
    DEFAULT_RETUNE_THRESHOLD_MBPS,
    DEFAULT_TIMEOUT,
    MAX_CONNECTIONS,
    PROBE_SEGMENTS_PER_CANDIDATE,
)
from .downloader import DownloadOptions, bench_connections, default_output_path, download_plan
from .metadata import get_metadata, normalize_video_id
from .models import Mp4Metadata, VideoSource
from .segments import active_sources, build_download_plan, select_source

console = Console()


def parse_connections(value: str) -> int:
    if value.strip().lower() == "auto":
        return 0
    try:
        return int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("connections must be 'auto' or an integer") from exc


def parse_header(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("headers must use 'Name: Value'")
    key, header_value = value.split(":", 1)
    key = key.strip()
    header_value = header_value.strip()
    if not key or not header_value:
        raise argparse.ArgumentTypeError("headers must use 'Name: Value'")
    return key, header_value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fast, resumable downloader for Abyss videos.")
    parser.add_argument("inputs", nargs="*", help="Video IDs, URLs, comma lists, or 'id h' style entries")
    parser.add_argument("-q", "--quality", help="best/high/h, medium/m, low/l/worst, exact label, or res_id")
    parser.add_argument("-o", "--output", help="Output file for a single video")
    parser.add_argument("--out-dir", default=".", help="Output directory for automatic filenames")
    parser.add_argument(
        "-c",
        "--connections",
        type=parse_connections,
        default=DEFAULT_CONNECTIONS,
        help=f"Parallel segment downloads: auto or 1-{MAX_CONNECTIONS}",
    )
    parser.add_argument("-H", "--header", action="append", type=parse_header, default=[], help="Extra HTTP header, repeatable")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help=f"Retries per segment, default {DEFAULT_RETRIES}")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"Network timeout in seconds, default {DEFAULT_TIMEOUT}")
    parser.add_argument(
        "--retune-below",
        type=float,
        default=DEFAULT_RETUNE_THRESHOLD_MBPS,
        help=f"Auto mode re-tests connections when batch speed falls below this MB/s, default {DEFAULT_RETUNE_THRESHOLD_MBPS}",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace the exact -o target if it exists")
    parser.add_argument("--no-resume", action="store_true", help="Ignore existing temp segments")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temp files after success")
    parser.add_argument("--list", action="store_true", help="Only list available qualities")
    parser.add_argument(
        "--bench",
        action="store_true",
        help="Fast speed test only: probe connection counts with ~12 MB of Range requests, no full download",
    )
    parser.add_argument(
        "--thorough",
        action="store_true",
        help="Use slow thorough auto-tuning (full segments per candidate, old behavior) instead of the fast 1 MB probe",
    )
    parser.add_argument(
        "--probe-segments",
        type=int,
        default=PROBE_SEGMENTS_PER_CANDIDATE,
        help=f"1 MB samples per candidate in --bench / fast auto-tune, default {PROBE_SEGMENTS_PER_CANDIDATE}",
    )
    parser.add_argument("--verbose", action="store_true", help="Show debug details")
    parser.add_argument("--input-file", help="Read video IDs or URLs from a file")
    args = parser.parse_args(argv)
    if args.connections > 0:
        args.connections = max(1, min(MAX_CONNECTIONS, args.connections))
    args.retries = max(0, args.retries)
    args.timeout = max(1, args.timeout)
    args.retune_below = max(0.0, args.retune_below)
    args.probe_segments = max(1, min(8, args.probe_segments))
    return args


def parse_input_entries(values: list[str]) -> list[tuple[str, str | None]]:
    text = " ".join(values).strip()
    if not text:
        return []
    entries: list[tuple[str, str | None]] = []
    for chunk in text.split(","):
        parts = chunk.strip().split()
        if not parts:
            continue
        entries.append((parts[0], parts[1] if len(parts) > 1 else None))
    return entries


def load_entries(args: argparse.Namespace) -> list[tuple[str, str | None]]:
    entries: list[tuple[str, str | None]] = []
    if args.input_file:
        for line in Path(args.input_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                entries.extend(parse_input_entries([line]))
    entries.extend(parse_input_entries(args.inputs))
    return entries


def headers_from_args(args: argparse.Namespace) -> dict[str, str]:
    return {key: value for key, value in args.header}


def quality_table(metadata: Mp4Metadata) -> Table:
    table = Table(title="Available qualities")
    table.add_column("#", justify="right")
    table.add_column("Label")
    table.add_column("res_id", justify="right")
    table.add_column("Codec")
    table.add_column("Size", justify="right")
    for index, source in enumerate(sorted(active_sources(metadata), key=lambda s: s.size), start=1):
        table.add_row(
            str(index),
            source.label or "unknown",
            str(source.res_id or ""),
            source.codec or "",
            format_bytes(source.size),
        )
    return table


def format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def prompt_for_quality(metadata: Mp4Metadata) -> str:
    console.print(quality_table(metadata))
    sources = sorted(active_sources(metadata), key=lambda s: s.size)
    choices = [str(i) for i in range(1, len(sources) + 1)]
    raw = Prompt.ask("Choose quality number, label, res_id, or alias", default=str(len(sources)))
    if raw in choices:
        return str(sources[int(raw) - 1].res_id)
    return raw


def prompt_for_output(default_path: Path) -> Path:
    raw = Prompt.ask("Output file", default=str(default_path))
    return Path(raw).expanduser()


def choose_source(metadata: Mp4Metadata, quality: str | None) -> tuple[VideoSource, str]:
    selected_quality = quality or prompt_for_quality(metadata)
    return select_source(metadata, selected_quality), selected_quality


def run_one(entry: tuple[str, str | None], args: argparse.Namespace, single: bool) -> Path | None:
    video_input, inline_quality = entry
    video_id = normalize_video_id(video_input)
    headers = headers_from_args(args)
    if args.verbose:
        console.print(f"[dim]Fetching metadata for {video_id}[/dim]")
    metadata = get_metadata(video_input, headers=headers, timeout=args.timeout)

    if args.list:
        console.print(quality_table(metadata))
        return None

    requested_quality = args.quality or inline_quality
    source, selected_quality = choose_source(metadata, requested_quality)
    plan = build_download_plan(video_id, metadata, source.res_id if source.res_id is not None else selected_quality)

    out_dir = Path(args.out_dir).expanduser()
    output = Path(args.output).expanduser() if args.output else default_output_path(video_id, source.label, out_dir)
    if not args.output and not requested_quality and sys.stdin.isatty():
        output = prompt_for_output(output)
    if args.output and not single:
        raise ValueError("-o/--output can only be used with one video")

    options = DownloadOptions(
        output=output,
        connections=args.connections,
        retries=args.retries,
        timeout=args.timeout,
        headers=headers,
        overwrite=args.overwrite,
        resume=not args.no_resume,
        keep_temp=args.keep_temp,
        status=lambda message: console.print(f"[dim]{message}[/dim]"),
        retune_below_mbps=args.retune_below,
        thorough=args.thorough,
    )

    if args.bench:
        from .constants import AUTO_CONNECTION_CANDIDATES, FAST_CONNECTION_CANDIDATES

        candidates = AUTO_CONNECTION_CANDIDATES if args.thorough else FAST_CONNECTION_CANDIDATES
        console.print(
            f"Benchmarking [bold]{video_id}[/bold] [{source.label or source.res_id}] "
            f"({len(candidates)} candidates x {args.probe_segments} x 1 MB probes)..."
        )
        import time as _time

        t0 = _time.perf_counter()
        results = bench_connections(
            plan,
            options,
            candidates=candidates,
            probe_segments=max(1, args.probe_segments),
            status=lambda m: console.print(f"[dim]{m}[/dim]"),
        )
        dt = _time.perf_counter() - t0
        table = Table(title=f"Bench results for {video_id} [{source.label or '?'}] ({dt:.1f}s, ~{sum(max(1, args.probe_segments) for _ in candidates)} MB)")
        table.add_column("Connections", justify="right")
        table.add_column("Speed", justify="right")
        table.add_column("Verdict", justify="left")
        best = max(results, key=lambda r: r[1]) if results else (0, 0.0)
        for conns, rate in sorted(results, key=lambda r: -r[1]):
            mark = " <-- best" if conns == best[0] else ""
            table.add_row(str(conns), f"{rate:.1f} MB/s", mark)
        console.print(table)
        if results:
            console.print(f"[green]Recommend:[/green] -c {best[0]} ({best[1]:.1f} MB/s)")
        return None

    connection_label = "auto-tuned connections" if args.connections <= 0 else f"{args.connections} connections"
    console.print(f"Downloading [bold]{video_id}[/bold] [{source.label or source.res_id}] with {connection_label} -> {output}")
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TextColumn("elapsed"),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("segments", total=plan.total_size)
        progress_lock = threading.Lock()

        def on_progress(bytes_done: int, _segments_done: int) -> None:
            if bytes_done:
                with progress_lock:
                    progress.update(task, advance=bytes_done)

        result = download_plan(plan, options, on_progress)
    console.print(f"[green]Saved:[/green] {result}")
    return result


def interactive_entry() -> list[tuple[str, str | None]]:
    raw = Prompt.ask("Video ID or Abyss URL")
    return parse_input_entries([raw])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    entries = load_entries(args)
    if not entries:
        entries = interactive_entry()
    if not entries:
        console.print("[red]No video IDs or URLs provided.[/red]")
        return 2

    try:
        for entry in entries:
            run_one(entry, args, single=len(entries) == 1)
        return 0
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return 130
    except Exception as exc:
        if args.verbose:
            console.print_exception()
        else:
            console.print(f"[red]Error:[/red] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

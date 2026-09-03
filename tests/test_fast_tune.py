"""Fast probe tuning tests (no network: _probe_bytes is stubbed)."""

from pathlib import Path

from abyssdl import downloader
from abyssdl.cli import parse_args
from abyssdl.constants import FRAGMENT_SIZE
from abyssdl.downloader import DownloadOptions, bench_connections
from abyssdl.models import DownloadPlan, Mp4Metadata, VideoSource


def _plan(n_tokens=12, size=None):
    size = size or FRAGMENT_SIZE * 2
    source = VideoSource("h264", "720p", None, None, 2, size, True, "v1", None)
    meta = Mp4Metadata(["x.abysscdn.com"], [source], "slug", 10)
    return DownloadPlan("abc", meta, source, "https://v1.abysscdn.com", size, ["t"] * n_tokens)


def _options(**kw):  # type: ignore[no-untyped-def]
    base = dict(output=Path("/tmp/out.mp4"), connections=0)
    base.update(kw)  # type: ignore[typeddict-item]
    return DownloadOptions(**base)  # type: ignore[arg-type]


def test_bench_picks_fastest_candidate(monkeypatch):
    plan = _plan()
    opts = _options()
    # Fake per-index speeds: higher index -> more bytes instantly. Instead,
    # stub _probe_bytes to return constant and control timing? Simpler: stub
    # _probe_many to return bytes proportional to workers (more conns faster).
    def fake_probe_many(plan, options, indexes, workers):
        return len(indexes) * 1024 * 1024  # 1 MB each, instant

    monkeypatch.setattr(downloader, "_probe_many", fake_probe_many)
    results = bench_connections(plan, opts, candidates=[8, 16], probe_segments=2)
    assert [c for c, _ in results] == [8, 16]
    assert all(rate > 0 for _, rate in results)


def test_bench_handles_probe_failure(monkeypatch):
    plan = _plan()
    opts = _options()

    def boom(plan, options, indexes, workers):
        raise IOError("net down")

    monkeypatch.setattr(downloader, "_probe_many", boom)
    results = bench_connections(plan, opts, candidates=[8, 16], probe_segments=1)
    assert results == [(8, 0.0), (16, 0.0)]


def test_cli_parses_bench_and_thorough():
    args = parse_args(["abc", "--bench"])
    assert args.bench is True
    assert args.thorough is False
    assert args.probe_segments == 3
    args = parse_args(["abc", "--bench", "--thorough", "--probe-segments", "5"])
    assert args.thorough is True
    assert args.probe_segments == 5
    args = parse_args(["abc", "--probe-segments", "99"])
    assert args.probe_segments == 8  # clamped

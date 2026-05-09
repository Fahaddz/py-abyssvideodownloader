import json

from abyssdl.constants import FRAGMENT_SIZE
from abyssdl.downloader import manifest_for, missing_segments, prepare_temp_dir, valid_segment
from abyssdl.models import Mp4Metadata, DownloadPlan, VideoSource


def plan(size=FRAGMENT_SIZE + 5):
    source = VideoSource("h264", "720p", None, None, 2, size, True, "v1", None)
    meta = Mp4Metadata(["x.abysscdn.com"], [source], "slug", 10)
    return DownloadPlan("abc", meta, source, "https://v1.abysscdn.com", size, ["a", "b"])


def test_resume_validation_identifies_missing_and_short_segments(tmp_path):
    download_plan = plan()
    output = tmp_path / "out.mp4"
    temp_dir = prepare_temp_dir(download_plan, output, resume=True)
    segments = temp_dir / "segments"
    (segments / "segment_0").write_bytes(b"x" * FRAGMENT_SIZE)
    (segments / "segment_1").write_bytes(b"x")

    assert valid_segment(segments / "segment_0", 0, download_plan.total_size)
    assert missing_segments(download_plan, temp_dir) == [1]


def test_bad_manifest_resets_temp_dir(tmp_path):
    download_plan = plan()
    output = tmp_path / "out.mp4"
    temp_dir = prepare_temp_dir(download_plan, output, resume=True)
    (temp_dir / "manifest.json").write_text(json.dumps({"bad": True}), encoding="utf-8")
    (temp_dir / "segments" / "segment_0").write_bytes(b"x" * FRAGMENT_SIZE)

    temp_dir = prepare_temp_dir(download_plan, output, resume=True)
    assert json.loads((temp_dir / "manifest.json").read_text(encoding="utf-8")) == manifest_for(download_plan)
    assert missing_segments(download_plan, temp_dir) == [0, 1]

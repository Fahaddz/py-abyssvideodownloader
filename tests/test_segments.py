import pytest

from abyssdl.constants import FRAGMENT_SIZE
from abyssdl.models import Mp4Metadata, VideoSource
from abyssdl.segments import (
    build_base_url,
    build_segment_tokens,
    expected_segment_size,
    select_source,
    total_segments,
)


def source(label, res_id, size):
    return VideoSource("h264", label, None, None, res_id, size, True, "v1", None)


def metadata():
    return Mp4Metadata(
        domains=["x.abysscdn.com"],
        sources=[source("360p", 1, 100), source("720p", 2, 200), source("1080p", 3, 300)],
        slug="slug",
        md5_id=99,
    )


def test_select_source_aliases_and_exact_values():
    meta = metadata()
    assert select_source(meta, "best").label == "1080p"
    assert select_source(meta, "m").label == "720p"
    assert select_source(meta, "low").label == "360p"
    assert select_source(meta, "720p").res_id == 2
    assert select_source(meta, "3").label == "1080p"
    with pytest.raises(ValueError):
        select_source(meta, "480p")


def test_segment_counts_and_last_sizes():
    assert total_segments(FRAGMENT_SIZE - 1) == 1
    assert total_segments(FRAGMENT_SIZE) == 1
    assert total_segments(FRAGMENT_SIZE + 1) == 2
    assert expected_segment_size(0, FRAGMENT_SIZE + 5) == FRAGMENT_SIZE
    assert expected_segment_size(1, FRAGMENT_SIZE + 5) == 5
    assert expected_segment_size(0, FRAGMENT_SIZE) == FRAGMENT_SIZE


def test_base_url_and_tokens():
    meta = metadata()
    selected = select_source(meta, "360p")
    assert build_base_url(meta, selected) == "https://v1.abysscdn.com"
    assert len(build_segment_tokens(meta, selected)) == 1

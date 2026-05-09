from __future__ import annotations

from .constants import FRAGMENT_SIZE
from .crypto import aes_ctr_encrypt, double_base64_encode, get_key
from .models import Mp4Metadata, DownloadPlan, VideoSource


def active_sources(metadata: Mp4Metadata) -> list[VideoSource]:
    return [source for source in metadata.sources if source.status and source.size > 0]


def source_sort_key(source: VideoSource) -> tuple[int, str]:
    return (source.size, source.label or "")


def select_source(metadata: Mp4Metadata, quality: str | int | None = None) -> VideoSource:
    sources = sorted(active_sources(metadata), key=source_sort_key)
    if not sources:
        raise ValueError("No valid sources found")

    if quality is None or str(quality).lower() in {"best", "high", "h"}:
        return sources[-1]

    q = str(quality).strip().lower()
    if q in {"low", "l", "worst"}:
        return sources[0]
    if q in {"medium", "m"}:
        return sources[(len(sources) - 1) // 2]

    for source in sources:
        if source.label and source.label.lower() == q:
            return source
        if source.res_id is not None and str(source.res_id) == q:
            return source

    raise ValueError(f"Quality {quality!r} was not found")


def total_segments(size: int) -> int:
    return (size + FRAGMENT_SIZE - 1) // FRAGMENT_SIZE


def expected_segment_size(index: int, total_size: int) -> int:
    count = total_segments(total_size)
    if index < count - 1:
        return FRAGMENT_SIZE
    last = total_size % FRAGMENT_SIZE
    return last or FRAGMENT_SIZE


def build_segment_tokens(metadata: Mp4Metadata, source: VideoSource) -> list[str]:
    enc_key = get_key(source.size)
    tokens = []
    for index in range(total_segments(source.size)):
        path = f"/mp4/{metadata.md5_id}/{source.res_id}/{source.size}/{FRAGMENT_SIZE}/{index}"
        tokens.append(double_base64_encode(aes_ctr_encrypt(path, enc_key)))
    return tokens


def build_base_url(metadata: Mp4Metadata, source: VideoSource) -> str:
    domain = metadata.domains[0] if metadata.domains else None
    root_domain = domain.split(".", 1)[1] if domain and "." in domain else domain
    if not root_domain:
        raise ValueError("Could not determine CDN domain")
    sub = source.sub or ""
    return f"https://{sub}.{root_domain}"


def build_segment_url(base_url: str, total_size: int, token: str) -> str:
    return f"{base_url}/sora/{total_size}/{token}"


def build_download_plan(video_id: str, metadata: Mp4Metadata, quality: str | int | None = None) -> DownloadPlan:
    source = select_source(metadata, quality)
    return DownloadPlan(
        video_id=video_id,
        metadata=metadata,
        source=source,
        base_url=build_base_url(metadata, source),
        total_size=source.size,
        tokens=build_segment_tokens(metadata, source),
    )

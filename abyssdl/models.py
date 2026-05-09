from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoSource:
    codec: str | None
    label: str | None
    part_size: int | None
    path: str | None
    res_id: int | None
    size: int
    status: bool
    sub: str | None
    url: str | None

    @classmethod
    def from_dict(cls, data: dict) -> "VideoSource":
        return cls(
            codec=data.get("codec"),
            label=data.get("label"),
            part_size=data.get("partSize"),
            path=data.get("path"),
            res_id=data.get("res_id"),
            size=int(data.get("size") or 0),
            status=bool(data.get("status")),
            sub=data.get("sub"),
            url=data.get("url"),
        )


@dataclass(frozen=True)
class Mp4Metadata:
    domains: list[str]
    sources: list[VideoSource]
    slug: str | None
    md5_id: int

    @classmethod
    def from_dict(cls, data: dict) -> "Mp4Metadata":
        return cls(
            domains=[d for d in (data.get("domains") or []) if d],
            sources=[VideoSource.from_dict(s) for s in (data.get("sources") or []) if s],
            slug=data.get("slug"),
            md5_id=int(data.get("md5_id")),
        )


@dataclass(frozen=True)
class DownloadPlan:
    video_id: str
    metadata: Mp4Metadata
    source: VideoSource
    base_url: str
    total_size: int
    tokens: list[str]

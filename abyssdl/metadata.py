from __future__ import annotations

import base64
import json
import re
import urllib.parse

from .client import fetch_text
from .constants import ABYSS_BASE, DEFAULT_TIMEOUT
from .crypto import aes_ctr_decrypt_str, get_key
from .models import Mp4Metadata


def normalize_video_id(value: str) -> str:
    value = value.strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme and parsed.netloc:
        query_id = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
        if query_id:
            return query_id.strip()
        tail = parsed.path.rstrip("/").split("/")[-1]
        if tail:
            return tail.strip()
    return value


def build_page_url(video_id_or_url: str) -> str:
    return f"{ABYSS_BASE}/?v={normalize_video_id(video_id_or_url)}"


def fetch_page(video_id_or_url: str, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    return fetch_text(build_page_url(video_id_or_url), headers, timeout)


def parse_metadata(html: str) -> Mp4Metadata | None:
    script_re = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
    for match in script_re.finditer(html):
        script = match.group(1)
        if "datas" not in script:
            continue
        datas_match = re.search(r'const\s+datas\s*=\s*"([^"]*)"', script)
        if not datas_match:
            continue
        datas_json = base64.b64decode(datas_match.group(1)).decode("iso-8859-1")
        datas = json.loads(datas_json)
        if "media" not in datas:
            continue

        media_key = f"{datas['user_id']}:{datas['slug']}:{datas['md5_id']}"
        decrypted = aes_ctr_decrypt_str(datas["media"], get_key(media_key))
        video = json.loads(decrypted)
        mp4 = video.get("mp4", {})
        mp4["slug"] = datas.get("slug")
        mp4["md5_id"] = datas.get("md5_id")
        return Mp4Metadata.from_dict(mp4)
    return None


def get_metadata(
    video_id_or_url: str,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Mp4Metadata:
    html = fetch_page(video_id_or_url, headers, timeout)
    metadata = parse_metadata(html)
    if metadata is None:
        raise ValueError("Could not extract video metadata. The video ID may be invalid or the page format changed.")
    return metadata

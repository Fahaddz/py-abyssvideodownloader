import base64
import json

from abyssdl.crypto import aes_ctr_encrypt, get_key
from abyssdl.metadata import normalize_video_id, parse_metadata


def build_fixture_html():
    datas = {"user_id": 7, "slug": "sluggy", "md5_id": 42}
    media = {
        "mp4": {
            "domains": ["a.abysscdn.com"],
            "sources": [
                {"label": "360p", "res_id": 1, "size": 100, "status": True, "sub": "s1", "codec": "h264"},
            ],
        }
    }
    key = get_key(f"{datas['user_id']}:{datas['slug']}:{datas['md5_id']}")
    datas["media"] = aes_ctr_encrypt(json.dumps(media), key)
    encoded = base64.b64encode(json.dumps(datas).encode("iso-8859-1")).decode("utf-8")
    return f'<html><script>const datas = "{encoded}"</script></html>'


def test_parse_metadata_from_datas_script():
    metadata = parse_metadata(build_fixture_html())
    assert metadata is not None
    assert metadata.slug == "sluggy"
    assert metadata.md5_id == 42
    assert metadata.sources[0].label == "360p"


def test_normalize_video_id_from_id_and_url():
    assert normalize_video_id("abc123") == "abc123"
    assert normalize_video_id("https://abysscdn.com/?v=abc123") == "abc123"

from pathlib import Path

from abyssdl.cli import load_entries, parse_args, parse_input_entries


def test_parse_comma_and_inline_quality():
    assert parse_input_entries(["id1 h,id2 m"]) == [("id1", "h"), ("id2", "m")]


def test_parse_args_headers_and_connection_clamp():
    args = parse_args(["abc", "-H", "Referer: https://x.test", "-c", "99", "--list"])
    assert args.connections == 64
    assert args.header == [("Referer", "https://x.test")]
    assert args.list is True


def test_parse_args_auto_connections():
    args = parse_args(["abc", "-c", "auto"])
    assert args.connections == 0


def test_parse_args_retune_threshold():
    args = parse_args(["abc", "--retune-below", "5.5"])
    assert args.retune_below == 5.5


def test_load_entries_from_file(tmp_path: Path):
    input_file = tmp_path / "videos.txt"
    input_file.write_text("id1 h\n# skip\nid2\n", encoding="utf-8")
    args = parse_args(["--input-file", str(input_file)])
    assert load_entries(args) == [("id1", "h"), ("id2", None)]

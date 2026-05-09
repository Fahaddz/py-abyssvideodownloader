# py-abyss

Credit: this project is a Python downloader-only rewrite inspired by the original [AbyssVideoDownloader](https://github.com/abdlhay/AbyssVideoDownloader) project.

`py-abyss` is a fast, resumable command-line downloader for Abyss video IDs and Abyss URLs. It is standalone Python: no Java, no Gradle, and no `abyss-dl.jar`.

## Features

- Download from a raw Abyss video ID or an Abyss URL.
- Interactive mode when no input is provided.
- Quality selection by `best`, `high`, `h`, `medium`, `m`, `low`, `l`, `worst`, exact label, or exact `res_id`.
- Auto connection tuning against the current CDN route.
- Adaptive re-tuning during a download with `--retune-below`.
- Resume support with validated segment files.
- Atomic final writes using `.part` output files.
- Stubborn retry and recovery passes for slow or failed segments.
- Batch input from a text file or comma-separated inline list.
- Repeatable custom HTTP headers.
- Clear progress display with speed, downloaded size, elapsed time, and ETA.

## Requirements

- Python 3.10 or newer.
- `uv`.
- Network access to `https://abysscdn.com`.

Install `uv` from https://docs.astral.sh/uv/.

## Quick Start

From this folder:

```powershell
uv run abyss-download 2QIvEC032 -q best
```

Windows launcher:

```powershell
.\download.bat 2QIvEC032 -q best
```

Show all options:

```powershell
uv run abyss-download --help
```

## Usage

Interactive mode:

```powershell
uv run abyss-download
```

Download best quality:

```powershell
uv run abyss-download 2QIvEC032 -q best
```

List available qualities:

```powershell
uv run abyss-download --list 2QIvEC032
```

Download an exact quality:

```powershell
uv run abyss-download 2QIvEC032 -q 720p
uv run abyss-download 2QIvEC032 -q 5
```

Use a fixed connection count instead of auto tuning:

```powershell
uv run abyss-download 2QIvEC032 -q best -c 24
```

Change the adaptive re-tune threshold:

```powershell
uv run abyss-download 2QIvEC032 -q best --retune-below 5.5
```

Use a custom output file:

```powershell
uv run abyss-download 2QIvEC032 -q best -o video.mp4
```

Batch input:

```powershell
uv run abyss-download --input-file videos.txt --out-dir downloads
uv run abyss-download "id1 h,id2 m,id3 l"
```

## Options

| Option | Description |
| --- | --- |
| `inputs` | Video IDs, Abyss URLs, comma lists, or entries like `id h`. |
| `-q, --quality VALUE` | `best`, `high`, `h`, `medium`, `m`, `low`, `l`, `worst`, exact label, or exact `res_id`. |
| `-o, --output FILE` | Output file path for a single video. |
| `--out-dir DIR` | Directory for automatic filenames. Defaults to the current directory. |
| `-c, --connections VALUE` | `auto` or a fixed number from `1` to `64`. Defaults to `auto`. |
| `-H, --header "Name: Value"` | Extra HTTP header. Can be repeated. |
| `--retries N` | Retries per segment. Default: `20`. |
| `--timeout SECONDS` | Network timeout per request. Default: `180`. |
| `--retune-below MBPS` | In auto mode, re-test connection counts when batch speed drops below this MB/s. Default: `6.0`. |
| `--overwrite` | Replace the exact `-o` target if it exists. |
| `--no-resume` | Ignore existing temp segments and start fresh. |
| `--keep-temp` | Keep temp segment files after a successful download. |
| `--list` | Only list available qualities. |
| `--verbose` | Show debug tracebacks for failures. |
| `--input-file FILE` | Read IDs/URLs from a file, one per line, with optional quality suffix. |

## Speed Tuning

Auto mode benchmarks real segments at several connection counts:

```text
8, 16, 24, 32, 48, 64
```

It keeps the downloaded benchmark segments, picks the fastest result, and continues with that connection count. During the download, it processes batches and checks the batch speed. If speed drops below `--retune-below`, it runs the connection benchmark again against the current CDN throttle state and continues with the new best value.

Use `-c N` to disable auto tuning and force a fixed connection count:

```powershell
uv run abyss-download 2QIvEC032 -q best -c 24
```

## Resume And Reliability

Downloads use a temp folder beside the output:

```text
.abyss-temp/<video_id>_<res_id>_<size>/
```

Each segment is written to `segment_N.part` first, then renamed to `segment_N` only after it reaches the expected size. On resume, complete segments are reused and incomplete segments are downloaded again.

The final MP4 is written to `<output>.part` and renamed only after all segments are validated and merged. If a fast pass leaves slow or failed segments behind, the downloader switches into low-concurrency recovery passes before it gives up.

If the output already exists, the downloader fails unless `--overwrite` is used. Auto-generated filenames avoid collisions by adding `_2`, `_3`, and so on.

## Project Layout

```text
download.py          Compatibility wrapper for abyss-download
download.bat         Windows uv launcher
pyproject.toml       Project metadata and uv entry point
uv.lock              Locked dependency versions
abyssdl/
  cli.py             Command-line interface
  client.py          HTTP helpers
  constants.py       Runtime defaults
  crypto.py          Abyss-compatible crypto helpers
  downloader.py      Parallel/resumable download engine
  metadata.py        Abyss page metadata extraction
  models.py          Dataclasses
  segments.py        Quality selection and segment token generation
tests/               Unit tests
```

## Troubleshooting

`uv is required`

Install `uv` and reopen the terminal.

`Could not extract video metadata`

The ID may be invalid, the video may be unavailable, or Abyss may have changed the page format.

`Output already exists`

Use a different filename, delete the existing file, or pass `--overwrite`.

Slow or unstable downloads

Use the default auto mode first. If the CDN behaves poorly, try a moderate fixed value:

```powershell
uv run abyss-download 2QIvEC032 -q best -c 16
uv run abyss-download 2QIvEC032 -q best -c 24
uv run abyss-download 2QIvEC032 -q best -c 32
```

Interrupted download

Run the same command again. Validated segments are reused from `.abyss-temp`.

Stale virtual environment

If the local `.venv` gets corrupted, delete it and let `uv` rebuild:

```powershell
Remove-Item -Recurse -Force .venv
uv sync
```

## Development

Install dependencies:

```powershell
uv sync
```

Run tests:

```powershell
uv run pytest
```

Compile check:

```powershell
$files = @('download.py') + (Get-ChildItem abyssdl -Filter *.py | ForEach-Object { $_.FullName })
uv run python -m py_compile @files
```

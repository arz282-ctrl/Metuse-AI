# Ucuts Audit (Phase 0)

## Pin

- Repository: https://github.com/arz282-ctrl/Ucuts.git (local copy audited at `/Users/ahmedxriyaz/Desktop/Ucuts`)
- Commit: `15469eeb1c327650d46731815486725085dd733d`
- License: MIT
- Status: pinned, not forked. Machine-readable pin: `packages/ucuts_adapter/UCUTS_PIN.json`.

## Docs vs actual CLI

`Ucuts/SKILL.md` (Standard Execution Workflow, step 3) shows `--reels 1,2,3`. The actual parser in `scripts/ucuts_pipeline.py` (`parse_args()`) has **no `--reels` flag**; `README.md` correctly uses `--sections`.

Real flags:

| Flag | Notes |
|---|---|
| `--url` (required) | URL or local file path |
| `--sections` | Comma-separated `HH:MM:SS-HH:MM:SS` ranges, default `00:01:30-00:02:30`; clips are named `reel_1..N` |
| `--model` | Whisper model size, default `small` |
| `--language` | Language code or `auto` |
| `--font` | Caption font, default `Noto Sans Devanagari` |
| `--output-dir` | Default `reels_output` |
| `--cookies-from-browser` | Passed to yt-dlp |
| `--avatar-idle`, `--avatar-talk`, `--avatar-hype` | Optional avatar frames |
| `--avatar-corner` | `bottom-left`, `bottom-right`, `top-left`, `top-right` |
| `--avatar-width` | Default 440 |
| `--keep-temp` | Keep `_tmp_work` |

## Verified capabilities (read from source)

- Section download via yt-dlp (`--download-sections`) or ffmpeg trim for local files
- 9:16 (1080x1920) face-tracked crop with OpenCV YuNet + Norfair (`mean_euclidean`)
- Border-connected chroma-key corner avatar overlay
- Word-level transcription with `stable-ts` (Whisper)
- Captions via `pysubs2` `.ass` + libass, with a Pango/HarfBuzz fallback when ffmpeg lacks libass
- Export: H.264 yuv420p, AAC 192k, loudnorm -14 LUFS, `+faststart`

## Gaps (must not be assumed)

- No autonomous highlight selection in the executable script (sections are supplied by the caller)
- No multi-tenant job isolation, accounts, billing
- No object-storage lifecycle
- No publishing or social account management
- No text/image-to-video generation

## Phase 4 engineering notes

1. `ensure_yunet_model()` downloads `face_detection_yunet_2023mar.onnx` from GitHub at runtime. Render workers run without network, so the model must be baked into the image.
2. `stable-ts` and `demucs` are archived upstream: pin exact versions in a dedicated environment.
3. `run_cmd` executes subprocess lists; the adapter must only map a typed, allowlisted operation schema to fixed commands. Never pass LLM-assembled shell strings.
4. URL input uses yt-dlp with optional browser cookies; the cloud adapter should accept only internal asset IDs (local files), not arbitrary URLs or cookie access.
5. Reconcile `SKILL.md` and README flags in adapter tests (timeline-to-command conversion tests).

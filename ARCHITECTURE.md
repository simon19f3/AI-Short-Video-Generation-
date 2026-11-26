# Architecture

`storyboard.json` is the approved input. The JSON Schema checks structure. Python additionally checks unique IDs, reference names, cue order, selected take, paired narration/subtitle text and the combined running time. The same validation precedes the radio cut and film.

| Stage | Input | Output |
|---|---|---|
| Narration | One shot's text, voice, delivery and model | Measured WAV, followed by a timeline-length WAV with silence padding |
| Keyframes | Shared visual style, reference prompts, one shot's image prompt | Reference portraits and per-shot PNGs |
| Shot visuals | One generated keyframe or original photo and its duration | FFmpeg holds the image throughout each shot, fitting the full picture in both formats. |
| Score | Running time, music cues, selected seed and backend | One instrumental WAV |
| Assembly | Picture segments, timeline voice, original score, subtitles and end card | Vertical and horizontal MP4s with stacked Amharic/English captions, separate SRTs and manifest |

The `film` command is the orchestrator. `providers.py` adapts model requests and `media.py` handles FFmpeg, subtitle timing and local synthesis. The radio command assembles audio before images. The draft command never overwrites an approved storyboard.

Each stage's cache key uses its local inputs, including hashes of upstream files. An update to one spoken line regenerates that WAV, its fitted timeline segment and the two final edits. An image edit affects the corresponding image and its assembled shot. A subtitle edit affects assembly. Changes to a shared setting can affect multiple shots. Failed jobs leave no completed cache entry. Media files are written atomically.

Gemini and Cloudflare retry transient connection errors, timeouts, and selected HTTP failures with backoff. ACE-Step submissions run one attempt at a time to avoid duplicating expensive jobs. The graduation storyboard uses the CPU-based FFmpeg still renderer for all shots. A configured ACE-Step failure stops the pipeline with an error. Clearing the ACE-Step URL selects local original music. A missing required Gemini or Cloudflare credential reports the exact variable name.

Cache roots for the real production run and offline preview are distinct. `runs.jsonl` records attempted model calls, stage timings and fallbacks with run IDs. Unmeasured cost is JSON null. Secrets and story prompt text are excluded from log parameters. The inputs' hash links the event to edited story data.

The Next.js studio forwards API requests to FastAPI. FastAPI launches the same Python commands, tracks one active job in memory, and serves completed MP4s through a range-capable route. A single Uvicorn worker owns the local job queue. Reference photos remain outside the web server’s public files.

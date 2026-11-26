# The Hands That Helped Me Grow

A storyboard-driven graduation short made from still images with ten shots, Amharic narration, bilingual subtitles, an original score, and an 80-second timeline. The story follows infancy, preschool, nursery, elementary school, high school, freshman-year uncertainty, university exploration, and graduation with family.

The portrait and graduation group photo are included in `private-references/` in this private ZIP. The pipeline uses the portrait and group photo as the still images for the final two shots, an architecture-only crop for the entrance shot, and the portrait to guide younger likenesses. Parents and friends in reenacted scenes are cast as stand-ins. Review the narrated details and the Amharic before release.

## Run the FastAPI backend and Next.js studio

Set up Python and FFmpeg as below and install Node.js 20.9 or newer. Open two VS Code terminals.

**Terminal 1**, in the `graduation-film` folder, starts FastAPI on port 8000:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

On macOS or Linux, use `.venv/bin/python` in place of the Windows executable. Keep this terminal open. Interactive API docs are at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

**Terminal 2**, also starting in `graduation-film`, runs Next.js:

```powershell
cd studio
npm ci
npm run dev
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). **Make radio cut** renders the black-screen narration cut. **Make video** runs the production pipeline and makes both MP4s available in the player. Check **Offline preview** to test the buttons without credentials or a GPU. FastAPI uses the same Python interpreter used to launch Uvicorn. Keep a single backend worker for the local job queue. Next.js forwards API requests to FastAPI. If you change the backend port, set `STUDIO_API_URL` before starting Next.js. The MP4 includes both subtitle lines and the player does not add a subtitle track.

## 1. Open the project in VS Code

Open the extracted **graduation-film** folder. Install Python 3.11 or 3.12 and a full [FFmpeg build](https://ffmpeg.org/download.html) that puts both `ffmpeg` and `ffprobe` on your PATH.

Windows PowerShell, from the VS Code terminal:

```powershell
py -3.12 -m venv .venv
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pipeline doctor
.\.venv\Scripts\python.exe -m pipeline film --preview
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m pipeline doctor
python -m pipeline film --preview
```

The offline preview renders both aspect ratios using labeled placeholder cards, timed Amharic and English subtitles and a local instrumental. The narrator is silent in the offline preview. Use it to verify editing and exports without API credentials.

## 2. Add provider credentials

| Stage | Default | Setup |
|---|---|---|
| Storyboard and subtitle drafting | Gemini 3.8 Flash, optional | Open [Google AI Studio API keys](https://aistudio.google.com/api-keys). Set `GEMINI_API_KEY` in `.env`. The project already contains a draft storyboard. |
| Amharic narration | Gemini 3.8 Flash TTS | The same Gemini API key. Set `GEMINI_TTS_MODEL=gemini-3.8-flash-lite-tts` if a short pronunciation comparison favors Lite. |
| Reference portraits and keyframes | Cloudflare Workers AI, FLUX.2 klein 4B | In the Cloudflare Workers AI dashboard find your account ID and create a token with Workers AI access for that account. Set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. |
| Still shots | FFmpeg | Holds each generated image or original photo for its storyboard duration. Runs locally on the CPU. |
| Music | Original local instrumental, with optional ACE-Step 1.5 Turbo | Configure your own ACE-Step REST server for model-generated music. The local mode synthesizes an original instrumental at no provider cost. |
| Assembly | FFmpeg | Runs locally. |

Gemini and Cloudflare publish free allowances for the selected services. Provider access depends on your account, region and quota. Verify both requests with `python -m pipeline smoke` before a full run. [Google pricing](https://ai.google.dev/gemini-api/docs/pricing) · [Gemini speech](https://ai.google.dev/gemini-api/docs/speech-generation) · [Cloudflare pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) · [FLUX model](https://developers.cloudflare.com/workers-ai/models/flux-2-klein-4b/)

### Optional ACE-Step score

Install [ACE-Step 1.5](https://github.com/ACE-Step/ACE-Step-1.5) in its own environment. From its checkout, run `uv sync`, then `uv run acestep-api`. Set `ACESTEP_URL=http://localhost:8001` in this project. If you enable authentication on the server, set the same `ACESTEP_API_KEY` here. This is a self-hosted API. A free hosted key for the chosen music model is not assumed.

Run `python -m pipeline music` to audition three takes in `outputs/production/music/`. After listening, set `music.selected` to `0`, `1` or `2` in `storyboard.json`. The film command generates or reuses only the selected take.

### Use the original graduation photos

Before generating with personal references, document permission in `ETHICS.md` and set `ALLOW_PERSONAL_REFERENCES=1`. The keyframe provider receives the portrait, derived references, and the architecture crop when their shots use them. FFmpeg reads the portrait and full group photo directly for the final two shots. Both exports fit the full image inside the frame, which may add borders where the aspect ratios differ. Keep `private-references/` out of the public repository.

## 3. Make the film

Use the virtual environment Python shown above. On Windows, replace `python` below with `.\.venv\Scripts\python.exe` if the environment is not active.

1. Review `storyboard.json`. Check your personal details, Amharic narration and English translation, and document likeness or voice permissions if you add real references.
2. Run `python -m pipeline validate` and `python -m pipeline smoke`.
3. Run `python -m pipeline radio`. Watch `outputs/production/radio-cut.mp4` with at least three intended viewers. Record feedback and edit the storyboard. The radio cut uses placeholder music on a black screen.
4. If you want to compare score candidates, run `python -m pipeline music` and select your preferred take. Leave `ACESTEP_URL` empty to use the local instrumental.
5. Run **`python -m pipeline film`** or **`make film`**. A clean checkout with the documented dependencies, credentials and local reference assets uses this same command. The vertical and horizontal MP4s, separate Amharic and English SRTs and run manifest are in `outputs/production/`.
6. Watch both exports and show the near-final cut to at least three intended viewers. Record what changed and rerun. Publish the finished film with the platform's AI disclosure enabled if posting publicly.
7. Record the walkthrough. Change one short narration line, rerun, and filter for event type `model_call` to show only that shot needing a new TTS call in `runs.jsonl`. Change the English subtitle too if needed for the final version.

For a new story, directly edit a copy of the storyboard or run `python -m pipeline draft --idea "My new story" --output drafts`. Review the suggested draft before running `python -m pipeline film --storyboard drafts/storyboard.draft.json --output outputs/another-film`.

## Submission evidence

Add actual audience observations to `DESIGN.md`, experiment outcomes to `EXPERIMENTS.md`, provider issues to `FAILURES.md`, and actual timings and cost evidence to `COSTS.md`. Summarize the results in your separate assignment report. Share the public repo and MP4, walkthrough and post links through the submission forms. `runs.jsonl` is ready for generation evidence. The outputs and cache are ignored by Git; share the final MP4s through your chosen submission links.

The two subtitle tracks are burned into the image and also available separately in `outputs/production/subtitles/`. Keep external subtitle loading off when playing the baked MP4s. If upgrading in place, an older `horizontal.en.srt` or `vertical.en.srt` next to the video can still be auto-loaded by a desktop player. Move those old files into another folder or use a fresh extraction.

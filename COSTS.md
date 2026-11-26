# Cost and runtime

Use `runs.jsonl` to compare stage wall time (stage_timing events) and per-attempt model latency (model_call events). Use the GPU provider's usage meter to record actual GPU minutes. Wan records job wall time when enabled, including model setup, which differs from utilization. ACE-Step call latency includes time waiting in its queue.

Record actual charges from your dashboards. For a list-price equivalent, record the published rate and date, provider usage (tokens or image units), and multiply GPU hours by the GPU rental rate. The pipeline leaves cost unknown where it has no billing feed. It records zero model/API fees for self-hosted jobs, which still use computing resources.

| Stage | Measured latency | GPU minutes | Actual charge | List-price equivalent |
|---|---|---|---|---|
| Draft and narration | Pending | Hosted | Pending | Pending |
| References and keyframes | Pending | Hosted | Pending | Pending |
| Motion | Pending | FFmpeg is CPU; record GPU use if Wan enabled | Pending | Pending |
| Score | Pending | Local score is CPU; record GPU use if ACE-Step enabled | Pending | Pending |
| Assembly | Pending | CPU | Local | Record runtime |

If you have access to a frontier model, compare one equivalent shot and record the same timing and cost fields.

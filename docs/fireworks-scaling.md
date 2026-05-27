# Fireworks Scaling Notes

Last checked against Fireworks docs: 2026-05-27.

This project uses Fireworks Serverless by default:

- Model: `accounts/fireworks/routers/kimi-k2p6-turbo`
- Endpoint: `https://api.fireworks.ai/inference/v1/chat/completions`
- Product surface: Fire Pass / Kimi K2.6 Turbo, unless `FIREPASS_MODEL` is changed

The important constraint is not "number of workers". It is token pressure over
time. Fireworks enforces serverless limits by token-per-minute buckets and an
account request envelope, so a small number of long-running coding agents can hit
429s faster than many short calls.

## What Fireworks Limits

Fireworks documents three adaptive serverless token limits:

- Total Prompt TPM: all input tokens, cached plus uncached
- Uncached Prompt TPM: input tokens that miss prompt cache
- Generated TPM: output tokens

The documented starting limits are:

- 3.6M Total Prompt TPM, about 60k prompt tokens per second
- 900k Uncached Prompt TPM, about 15k uncached prompt tokens per second
- 36k Generated TPM, about 600 generated tokens per second

Fireworks also has a fixed account-wide request-rate envelope. With a payment
method on file, the documented maximum is 6,000 RPM across the account. That cap
is separate from the adaptive serverless TPM limits.

Read the response headers on every successful call:

- `X-Ratelimit-Limit-Tokens-Prompt`
- `X-Ratelimit-Limit-Tokens-Cache-Adjusted-Prompt`
- `X-Ratelimit-Limit-Tokens-Generated`
- `fireworks-prompt-tokens`
- `fireworks-cached-prompt-tokens`

Those headers should drive concurrency. Static config alone will be wrong
because adaptive limits grow and shrink.

Sources:

- https://docs.fireworks.ai/serverless/rate-limits
- https://docs.fireworks.ai/serverless/overview
- https://docs.fireworks.ai/guides/quotas_usage/account-quotas

## 429 vs 503

Treat these as different signals:

- `429 Too Many Requests`: serverless token/request limits were exceeded, or a
  dedicated deployment is saturated. For this repo's default serverless path,
  reduce concurrency and retry with exponential backoff.
- `503 Service Overloaded`: Fireworks may be load shedding during high demand.
  Retry with backoff. Priority tier can reduce 503s, but it does not create a
  separate rate-limit pool for a given model.

For dedicated or on-demand deployments, a 429 is usually a capacity signal: too
many queued/active requests for the deployment's GPUs. The fix there is to lower
burst concurrency, scale replicas/GPUs, or reduce request size.

Sources:

- https://docs.fireworks.ai/guides/inference-error-codes
- https://docs.fireworks.ai/serverless/priority-and-fast
- https://docs.fireworks.ai/guides/ondemand-deployments

## Can We Launch 10 Workers in Parallel?

Not as a cold default.

For Fire Pass / serverless, there is no user-controlled replica scale-up step.
Fireworks adapts account/model limits based on usage. Launching 10 workers at
once is only reasonable after the observed headers and recent error rate show
there is headroom.

The generated-token bucket is likely the first bottleneck. Fast Kimi variants
aim for high per-request generation speed, while the documented starting
Generated TPM is about 600 generated tokens per second. Ten active agents all
streaming large responses can consume that quickly. The tool loop also repeats
large prompts, so prompt TPM matters too.

For on-demand deployments, "10 workers" means something different: first size
the deployment and autoscaler, keep enough minimum replicas warm for the desired
latency, benchmark with realistic payloads, then increase client concurrency.

Sources:

- https://docs.fireworks.ai/serverless/rate-limits
- https://docs.fireworks.ai/deployments/autoscaling
- https://docs.fireworks.ai/deployments/benchmarking

## Recommended Scale Path

Use this as the project policy until the runtime has a real adaptive limiter.

1. Start with one active Fireworks model call per MCP server process.
2. Allow `firepass_trio`, but remember it is sequential inside one call:
   researcher, worker, reviewer, and optional fix loops.
3. Add telemetry before increasing concurrency:
   - response status counts
   - rate-limit headers
   - prompt tokens and cached prompt tokens
   - generated token rate if available
   - retry count and final failure status
4. Ramp concurrency gradually: 1 -> 2 -> 3 -> 5 -> 10. Hold each step long
   enough to see several complete agent loops, not just one API request.
5. Automatically reduce concurrency on any 429. A good first policy is halve the
   allowed concurrency on 429, then ramp up again only after a clean window.
6. Keep 30 to 40 percent token headroom. A simple safe-worker estimate is:

   ```text
   # Convert Fireworks token-per-minute limits to token-per-second limits first.
   safe_workers = floor(
     0.6 * min(
       prompt_tps_limit / observed_prompt_tps_per_worker,
       uncached_prompt_tps_limit / observed_uncached_prompt_tps_per_worker,
       generated_tps_limit / observed_generated_tps_per_worker
     )
   )
   ```

7. Treat 10 workers as an explicit high-concurrency mode, not the default.
8. If a launch needs high parallelism from minute one, talk to Fireworks for a
   custom solution instead of relying on the adaptive ramp.

## Prompt Cache Policy

Prompt caching helps, but it does not remove all rate limits. Total Prompt TPM
still counts cached and uncached input tokens. Cache hits mainly reduce uncached
prompt pressure, latency, and backend work.

Keep the current message shape cache-friendly:

- Static system prompt first.
- Tool schema stable.
- Dynamic task/context later in the request.
- No timestamps or volatile strings at the start of prompts.

When adding headers, use `x-session-affinity` per agent run, not globally. A
single global key can concentrate unrelated parallel workers onto one replica.
A per-run key keeps one worker's iterative tool loop sticky while still allowing
parallel workers to distribute.

Source:

- https://docs.fireworks.ai/guides/prompt-caching

## Runtime Changes Worth Making Next

These are concrete implementation recommendations, in order:

1. Add retry with exponential backoff and jitter around the `_stream_response`
   call site for `429`, `408`, `502`, `503`, `504`, and transient transport
   errors.
2. Add a process-wide concurrency gate so concurrent MCP calls cannot stampede
   Fireworks. Seed it conservatively at 1.
3. Capture response headers from `httpx` and expose a compact telemetry record in
   the XML activity or stderr logs.
4. Add an adaptive limiter keyed by model:
   - shrink immediately on 429
   - slowly increase after clean windows
   - respect all three observed token headers
5. Add per-run `x-session-affinity` to improve prompt cache behavior inside a
   worker loop.
6. Add a documented high-concurrency mode only after the limiter exists.

Do not add a fixed "10 workers" flag before this. That would encode a number
without the measurements needed to know whether it is safe.

## Fire Pass Boundary

Fire Pass is documented for personal agentic coding use. It is not the scale
path for production workloads, team/shared usage, or load testing as a service.
If this project becomes a shared or production service, move the discussion to
standard serverless billing, Priority/Fast where appropriate, or on-demand
deployments with autoscaling.

Source:

- https://docs.fireworks.ai/firepass

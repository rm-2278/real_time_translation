# Research Agent Playbook

You are resuming an autonomous research pipeline for the `real_time_translation`
repo (Zoom/YouTube English->Japanese live-subtitle SimulST system). You are
likely a **fresh Claude session with no memory of any prior conversation** --
possibly woken up by a cron schedule. Everything you need is either in this
file or in `research_agent/state/*.json`. Read this whole file before doing
anything.

## Goal

Run the research loop below to find and validate concrete improvements to
this repo's real-time ASR->LLM-translation pipeline, grounded in simultaneous
speech translation literature, and to keep a truthful, resumable record of
progress so the human (rm-2278) never has to babysit it.

```
SEARCH_PAPERS -> EXTRACT_PAPERS -> READ_PAPERS -> GENERATE_HYPOTHESES
   -> HUMAN_APPROVAL (-> WAITING_APPROVAL if escalated) -> RUN_EXPERIMENTS
   -> ANALYZE_RESULTS -> WRITE_REPORT -> REFLECT -> back to SEARCH_PAPERS
      or GENERATE_HYPOTHESES
```

## Step 0: orient yourself every time

```bash
# cd to wherever this repo's working copy actually is in THIS session --
# do not assume a fixed path. A local session may be at
# /Users/riki/Desktop/GitHub/real_time_translation, but a cloud/scheduled
# session (found cycle 10, 2026-09-12) gets a fresh clone at a different
# path each time (e.g. /home/user/real_time_translation) and on a branch
# other than main (the pipeline currently lives on
# feat/rm2278/async-containers -- `git branch -a` / `git log --oneline`
# if unsure which branch has research_agent/).
git status && git log --oneline -5
python3 research_agent/orchestrator.py status
cat research_agent/state/hypotheses.json
tail -c 3000 research_agent/state/reflections.md 2>/dev/null
```

The `current_state` field tells you which section below to execute. Do
**one bounded unit of work** for that state (not the whole remaining
pipeline) per invocation, then advance the state and commit. If you have
budget/time left after finishing a unit cleanly, you may continue into the
next state in the same session -- but always leave the repo in a
consistent, committed state before you stop, since you may be interrupted
(context limit, session end) at any point.

Note on the two "cycle" counters (found cycle 9, confirmed still true
cycle 10): `pipeline_state.json`'s own `cycle` field only increments on a
`REFLECT -> SEARCH_PAPERS` transition (see orchestrator.py), so it under-
counts whenever REFLECT instead advances straight to `GENERATE_HYPOTHESES`
(a valid, deliberate shortcut this playbook explicitly allows). Report
filenames and commit messages instead narrate a *different*, larger
"cycle N" that increments on every REFLECT regardless of which state it
advances to -- that is the number to use when naming
`research_agent/reports/YYYYMMDD_cycleN_report.md` and in commit messages,
NOT the raw JSON field. Keep using the narrated count for anything
human-facing; treat the JSON field as an internal
"REFLECT->SEARCH_PAPERS loop count" only.

## State: SEARCH_PAPERS

- Use WebSearch to look for simultaneous speech translation / streaming MT
  research published after the existing literature base (see
  `research_agent/state/papers.json` -- everything with `"source": "survey"`
  is already-known baseline literature from a 2026-09-08 survey). Look
  specifically for things the survey flagged as caveats or gaps: newer
  cascade-vs-end-to-end comparisons, new stability/flicker policies, new
  LLM-as-MT prompting techniques, updated vendor ASR latency numbers.
- Prefer arXiv, ACL Anthology, Semantic Scholar, OpenReview (open access,
  no login needed). Do not attempt to set up browser automation -- it was
  explicitly decided WebSearch/WebFetch are sufficient.
- For each promising paper, append a stub entry to `papers.json` with
  `"status": "found"` and whatever metadata you have (title/url/venue/year).
- Advance: `python3 research_agent/orchestrator.py advance EXTRACT_PAPERS --note "..."`

## State: EXTRACT_PAPERS

- For each `"status": "found"` paper, WebFetch the abstract (and intro/
  conclusion if the fetch gives you the full text) to pull out the concrete
  claims/numbers, not just the abstract's marketing language.
- If WebFetch is EGRESS_BLOCKED for the paper's host (found cycle 14,
  2026-09-16: arxiv.org/aclanthology.org/semanticscholar.org/and other
  mirrors were ALL blocked that session, a broader block than the
  RUN_EXPERIMENTS section's host-specific Deepgram/Gemini API notes below),
  fall back to WebSearch with a query like `"<exact paper title>" abstract`
  -- it goes through a different path and returned usable abstract-level
  content even when every WebFetch call failed. Note in the paper's entry
  which method was used.
- Update `status` to `"extracted"`.
- Advance to `READ_PAPERS`.

## State: READ_PAPERS

- Turn each extracted paper into a real `summary` (2-4 sentences, in your
  own words) and 2-5 `key_findings` bullets in `papers.json`. Mark
  `status: "read"`.
- Cross-reference against what's already in `experiments/results.csv` and
  `experiments/*.json` (50+ prior runs) -- do NOT propose re-testing
  something already conclusively answered there (e.g. chunk-length/
  endpointing sweeps and ASR keyterm on/off are already covered; see the
  filenames under `experiments/`).
- Do this cross-reference by actually grepping `experiments/results.csv`
  (the `notes`/`experiment_name` columns) and `experiments/*.json`
  filenames for topical keywords related to each candidate hypothesis, and
  by skimming `git log --oneline -20 -- experiments/ src/` for recent
  human-authored commits -- not just eyeballing a directory listing. Found
  cycle 14 (2026-09-16): a hypothesis about compressing long translations
  via a prompt instruction almost got written up as "nothing existing
  tests this" despite `experiments/20260915_budget_translation_*.json`
  (visible in the same `ls experiments/` output, landed via commits
  `33faed4`/`c4fd798` the day before) being a closely related,
  already-tested feature with a documented fidelity cost -- caught before
  running anything, during RUN_EXPERIMENTS design, but only because the
  code was read closely at that point, not because the READ_PAPERS
  cross-reference actually caught it. A keyword grep would have caught it
  directly.
- Advance to `GENERATE_HYPOTHESES`.

## State: GENERATE_HYPOTHESES

- Write 1-3 new concrete, testable hypotheses into `hypotheses.json`
  following the existing schema. Each hypothesis MUST specify:
  `required_changes`, `uses_existing_clips` (bool), and
  `estimated_cost_usd` (rough Deepgram+LLM spend for the new experiment
  runs it needs -- 0 if it's a purely retroactive analysis over existing
  experiment JSON, which is the cheapest and highest-priority kind of
  hypothesis to pursue).
- Do not let the backlog exceed ~6 `queued`/`proposed` hypotheses at once --
  prioritize depth (fully testing and reflecting on a few) over breadth.
- If a hypothesis's `required_changes` references a specific
  `TimedEvent`/experiment-JSON event field (e.g. `is_utterance_end`,
  `original_text`, anything under `results.events`), check the actual
  construction sites in `video_segment.py`/`youtube_segment.py`'s
  `on_result()` for which `kind` values that field is really populated on
  before finalizing the hypothesis text -- do not assume a field is
  populated uniformly across event kinds just because the dataclass
  defines it once. This has been the wrong assumption behind three
  separate near-misses now (h-gemini-only-masking-replay's `original_text`-
  empty discovery, h-cross-utterance-flicker's missing `utterance_id`, and
  cycle 13's `is_utterance_end`-only-set-on-translation-events discovery --
  see reflections.md cycle 13).
- Advance to `HUMAN_APPROVAL`.

**Human-requested literature refresh mid-cycle (found cycle 15,
2026-09-16):** if a human interactively asks for a fresh literature/blog
pass while `current_state` is already past `SEARCH_PAPERS` (e.g. sitting in
`GENERATE_HYPOTHESES`), do NOT try to force the orchestrator backward
through `SEARCH_PAPERS -> EXTRACT_PAPERS -> READ_PAPERS` -- that is not a
legal transition (see `TRANSITIONS` in orchestrator.py) since REFLECT is
the only state that can re-enter `SEARCH_PAPERS`. Instead, just do the
WebSearch/WebFetch/papers.json-update work informally in place, note this
plainly in papers.json (a dated `_websearch_note_cycleN` key, as prior
cycles already do) and in the cycle's reflections.md entry, and proceed
straight to writing hypotheses. This is sanctioned, not a deviation to
flag as a problem.

## State: HUMAN_APPROVAL

For every hypothesis with `approval` still unset or `"proposed"`:

```bash
python3 research_agent/orchestrator.py check-budget <estimated_cost_usd>
```

- If it prints `AUTO_APPROVE` **and** `uses_existing_clips` is `true`: set
  `"approval": "auto_approved"`, `"status": "queued"` in `hypotheses.json`.
  This is the normal path -- current policy is: reuse cached YouTube clips
  under `experiments/refs/`, no new downloads, per-batch cap $3, daily cap
  $7 (see `research_agent/state/budget.json`).
- If it prints `ESCALATE_TO_HUMAN`, OR the hypothesis needs a new YouTube
  clip/download, OR it requires touching the live production pipeline
  paths that are not behind an experiment-only flag: set
  `"approval": "needs_human"`, write a short entry to
  `research_agent/state/pending_approval.json` (create it if absent; a
  list of `{hypothesis_id, reason, estimated_cost_usd, asked_at}`), and
  notify the user (use the PushNotification tool if available; otherwise
  make sure it's clearly flagged in the next Japanese report). Do NOT run
  that experiment. Move on to other queued hypotheses instead of blocking.
- Advance to `RUN_EXPERIMENTS` once at least one hypothesis is `queued`
  with `approval != "needs_human"`. If none are approvable right now,
  advance to `WAITING_APPROVAL` instead and stop for this session.

## State: WAITING_APPROVAL

- Check `pending_approval.json`. If the human has approved an entry
  (however they signal it -- e.g. editing the file, or a later message in
  a live session), move that hypothesis's `approval` to
  `"approved_by_human"`, `"status": "queued"`, remove it from
  `pending_approval.json`, and advance to `RUN_EXPERIMENTS`.
- Otherwise, just re-check `hypotheses.json` for anything that's already
  auto-approvable (e.g. a new hypothesis added since last run) and advance
  to `RUN_EXPERIMENTS` if so. If truly nothing is runnable, stay in
  `WAITING_APPROVAL` and stop for this session (no-op is fine -- do not
  force progress that would violate the approval gate).

## State: RUN_EXPERIMENTS

- **First, verify the environment can actually run a live experiment**
  before picking anything that needs one:
  ```bash
  echo "DEEPGRAM_API_KEY set: $([ -n "$DEEPGRAM_API_KEY" ] && echo yes || echo no)"
  echo "LLM key set: $([ -n "$GOOGLE_API_KEY$OPENAI_API_KEY" ] && echo yes || echo no)"
  command -v ffmpeg >/dev/null && echo "ffmpeg: ok" || echo "ffmpeg: MISSING"
  # If missing, try `apt-get update && apt-get install -y --no-install-recommends
  # ffmpeg` before giving up -- worked cleanly in a 2026-09-08 cloud sandbox
  # (had sudo/apt-get available; some mirror 404s on unrelated GPU-driver
  # packages were harmless noise, ffmpeg itself still installed fine).
  # UPDATE cycle 8 (2026-09-11): this is NOT reliably one-shot -- in this
  # session the same class of unrelated-package mirror failures (libva2
  # connection-failed, libssh-gcrypt-4/libcaca0 404s) caused the whole
  # transaction to abort even though ffmpeg's own .deb had already
  # downloaded. `apt-get update` first, then retrying with `--fix-missing`,
  # is worth trying once, but don't loop on it indefinitely or block other
  # states on it -- if it hasn't succeeded after ~2 attempts, treat ffmpeg
  # as unavailable for this cycle and move on (this is moot anyway whenever
  # the separate Deepgram listen-websocket check below is also failing,
  # since no live audio experiment can run either way).
  ls experiments/refs/audio/*.webm 2>/dev/null || echo "no cached clips found"
  # Keys being *set* isn't the same as being *reachable* -- a cloud
  # sandbox's network egress proxy can allow one API host and reject
  # another independently (observed 2026-09-08: generativelanguage.
  # googleapis.com reachable, api.deepgram.com rejected with a 403 at
  # the proxy/CONNECT level, org policy, even with a valid key). Check
  # both directly instead of assuming a set key means a reachable API:
  curl -sS -o /dev/null -w "deepgram REST reachable: %{http_code}\n" \
    -H "Authorization: Token $DEEPGRAM_API_KEY" https://api.deepgram.com/v1/projects
  curl -sS -o /dev/null -w "gemini reachable: %{http_code}\n" \
    "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY"
  # IMPORTANT (found cycle 3, 2026-09-08, same day as the note above but a
  # different session): a 200 on the REST check above is NOT sufficient
  # evidence that a live experiment can actually run. Observed same day:
  # REST /v1/projects returned 200, but the real experiment runner's
  # Deepgram *listen/streaming websocket* handshake still failed with a
  # distinct 403 ("Unexpected error when initializing websocket
  # connection"), confirmed independent of keyterms. REST and WS-upgrade
  # traffic to the same host can be allowed/blocked independently by an
  # egress proxy (or Deepgram itself may scope REST vs streaming access
  # differently for some keys) -- do not assume one implies the other.
  # Test the actual websocket endpoint the experiment runner will use
  # (requires the venv's `deepgram-sdk`/`websockets` deps installed --
  # see the environment-setup note below if `uv sync` fails):
  python3 -c "
import asyncio, os, ssl
import websockets

async def try_connect(ctx, label):
    url = 'wss://api.deepgram.com/v1/listen?model=nova-2-general&language=en&encoding=linear16&sample_rate=16000'
    headers = {'Authorization': f\"Token {os.environ.get('DEEPGRAM_API_KEY', '')}\"}
    try:
        async with websockets.connect(url, additional_headers=headers, ssl=ctx):
            print(f'deepgram listen-websocket ({label}): OK')
        return True
    except websockets.exceptions.InvalidStatus as e:
        # A real response FROM Deepgram (or a proxy speaking HTTP on its
        # behalf) -- this is the signal that actually matters, e.g. cycle-5
        # sandbox: HTTP 400 \"Connection header did not include 'upgrade'\",
        # meaning the egress proxy is mangling the WS upgrade handshake.
        print(f'deepgram listen-websocket ({label}): FAILED, HTTP {e.response.status_code}: {e.response.body!r}')
        return False
    except Exception as e:
        print(f'deepgram listen-websocket ({label}): FAILED ({type(e).__name__}: {e})')
        return False

async def main():
    ctx = ssl.create_default_context()
    cafile = os.environ.get('SSL_CERT_FILE')
    if cafile:
        try:
            ctx.load_verify_locations(cafile=cafile)
        except ssl.SSLError:
            pass  # fall through to the cert-verified attempt anyway; it may still work
    if await try_connect(ctx, 'cert-verified'):
        return
    # cycle-5 finding: a TLS-terminating egress proxy's injected CA can trip
    # OpenSSL 3.x's strict key-usage-extension check ('CA cert does not
    # include key usage extension') even though the proxy and Deepgram are
    # both otherwise reachable -- this is a red herring, not the real
    # blocker. Retry with verification off to see what's actually behind it.
    ctx_noverify = ssl.create_default_context()
    ctx_noverify.check_hostname = False
    ctx_noverify.verify_mode = ssl.CERT_NONE
    await try_connect(ctx_noverify, 'cert-verification-disabled, diagnostic only')

asyncio.run(main())
"
  ```
  # A result from the disabled-verification attempt only tells you what's
  # blocking the connection -- it is NOT itself sufficient evidence that a
  # real experiment can run, since the actual runner (video_segment.py /
  # youtube_segment.py, via the `deepgram` SDK) does proper cert
  # verification and would fail even if this diagnostic script "succeeds"
  # only with verification off. If the cert-verified attempt fails but you
  # need to know *why* before giving up, use the disabled-verification
  # retry to diagnose, then still treat the hypothesis as blocked unless the
  # cert-verified attempt itself prints OK.
  # Environment-setup note (found cycle 3): `uv sync`/`uv run` in this repo
  # can fail even for a plain (non-zoom) experiment, because uv locks
  # ALL optional-dependency groups together by default, including the
  # `zoom` extra's `rtms` package which only exists on `test.pypi.org` --
  # a host this sandbox's egress proxy has rejected (distinct from the
  # `api.deepgram.com`/`generativelanguage.googleapis.com` blocks above,
  # and unrelated to them). Experiments never need the zoom extra
  # (`Config.from_env(require_zoom=False)`). This repo also requires
  # Python >=3.13 -- if `python3 -m venv` picks up a 3.11/3.12
  # interpreter, `uv pip install -e ".[experiments]"` will fail to
  # resolve; check `uv python list` for an already-installed 3.13 (e.g.
  # `/usr/bin/python3.13`) and use that explicitly:
  # `python3.13 -m venv .venv && source .venv/bin/activate && uv pip
  # install -e ".[experiments]"` (uv pip install does a normal
  # per-package resolve, not a universal all-extras lock) instead of
  # `uv sync`/`uv run`.
  # IMPORTANT (found cycle 4): that workaround fixes *install* time, but
  # `uv run <console-script-name>` (e.g. `uv run
  # real-time-translation-exp-flicker`) still re-triggers a full `uv
  # sync` first and hits the same zoom/rtms testpypi block, even inside
  # an already-installed `.venv`. For any script that needs no live
  # ASR/LLM API access (retroactive analysis scripts like
  # flicker_metrics.py), skip `uv run` entirely and call the module
  # directly through the activated venv's own python instead, e.g.:
  # `source .venv/bin/activate && python3 -m
  # real_time_translation.experiments.flicker_metrics` (check
  # `pyproject.toml`'s `[project.scripts]` table for the
  # console-script-name -> module:function mapping). This only avoids
  # `uv run`'s sync step -- a script that genuinely needs the `zoom`
  # extra still needs that dependency resolved some other way, which no
  # experiment does.
  # IMPORTANT (found cycle 5): `ruff` is NOT pulled in by `.[experiments]`,
  # so even `python3 -m ruff` fails with "No module named ruff" in a venv
  # set up via the workaround above, and `uv run ruff check .` fails the
  # same zoom/rtms-sync way as any other `uv run`. Run `uv pip install
  # ruff` once (a normal per-package install, not a full sync) in the
  # activated venv, then use `python3 -m ruff check <paths>` directly.
  # IMPORTANT (found 2026-09-18, a human-driven local session, not a
  # cloud sandbox cycle): `Config.from_env()` calls `load_dotenv(
  # override=True)` -- a deliberate choice (see its own comment: fixes a
  # real past bug where a stale shell OPENAI_API_KEY shadowed a corrected
  # .env value with no visible error). The side effect: shell-exporting a
  # one-off override for THIS run (e.g. `DICTIONARY_PATH=... uv run ...`)
  # is SILENTLY DISCARDED for any variable that also has a line in `.env`
  # -- .env wins every time, no error, no warning. Cost a full 75-minute
  # live run (experiments/20260918_llm2025_ep8_zenhan_full_lecture.json)
  # re-run from scratch: DICTIONARY_PATH was shell-exported to a
  # session-specific glossary but silently reverted to .env's own
  # DICTIONARY_PATH=dictionary.csv value every time. Variables NOT present
  # in `.env` (e.g. DEEPGRAM_MAX_INTERIM_DURATION, READING_SPEED_*,
  # CONTINUATION_TRANSLATION_MODE) export/override fine, as does the
  # `--endpointing` CLI flag (applied to `config` after `from_env()`
  # returns, never touched by dotenv at all) -- this only bites variables
  # that collide with an existing `.env` line. `video_segment.py`/
  # `youtube_segment.py` expose no `--env-file` flag to point at an
  # alternate file, so the only reliable one-off override for a
  # `.env`-shadowed variable is to edit `.env` itself for the run, then
  # revert it afterward -- check what a variable's shell-export actually
  # took effect via the experiment JSON's own `config` block before
  # trusting a long/expensive run, not just before it starts.
  If any of these fail (this can legitimately happen -- e.g. a cloud
  sandbox where secrets haven't been provisioned yet, a fresh
  environment without ffmpeg, or -- distinct from a missing/bad key --
  the egress proxy blocking one specific API host, or even one specific
  protocol (REST vs websocket) on a host, while others remain
  reachable), **do not treat it as a crash**: log a
  clear note (`python3 research_agent/orchestrator.py advance
  RUN_EXPERIMENTS --note "blocked: <what's missing>"` is not itself a
  legal transition, so just append the reason to the hypothesis's
  `result_summary` as `"blocked_reason": "..."` instead, e.g. via a
  dedicated field), skip straight to $0/no-new-experiment hypotheses if
  any remain queued, otherwise advance to `WRITE_REPORT` anyway (mention
  the environment gap in the Japanese report so the human can fix it) and
  then `REFLECT`. Never fabricate experiment results if the environment
  can't actually run one.
- If the environment check passes: pick one `queued` hypothesis (prefer
  $0 retroactive-analysis hypotheses first, then cheapest
  `estimated_cost_usd`).
- Implement `required_changes` if it needs code (small, focused diff --
  follow this repo's existing style, run `uv run ruff check .` after).
- Run the experiment via the existing runner, e.g.:
  ```bash
  uv run real-time-translation-exp-youtube --url <cached clip URL already
    used in experiments/refs/> --start <..> --end <..> --name <exp_name> \
    --domain <..>
  ```
  Reuse a clip/time-range already present under `experiments/refs/` --
  do not download new YouTube content under current policy.
- **Immediately after the run**, estimate actual spend (Deepgram audio
  seconds + LLM tokens are visible in the experiment JSON's `results` and
  `models` sections) and log it:
  ```bash
  python3 research_agent/orchestrator.py log-cost <actual_usd> --note "<exp_name>"
  ```
- Follow `CLAUDE.md`'s experiment logging rule: the runner already writes
  `experiments/YYYYMMDD_<name>.json` and appends to `experiments/results.csv`.
  Set `hypothesis.experiment_ids` to the new JSON path(s).
- Set `hypothesis.status = "testing"`. Commit (`git add`, `git commit`) now
  -- do not wait until analysis is done, in case you get interrupted.
- Advance to `ANALYZE_RESULTS`.

## State: ANALYZE_RESULTS

- **Before trusting any cross-run metric comparison, diff the new
  experiment JSON's `config` block against its baseline's `config` block**
  (`python3 -c "import json; ..."` on both files is enough -- no need for
  a dedicated script). Confirm the *only* difference is the variable the
  hypothesis is testing. This step exists because of a cycle-12 near-miss:
  a human-run experiment for `h-masking-holdback` looked like a ~47%
  flicker improvement until a `config` diff revealed `gemini_rpm_limit=9`
  vs. the baseline's `60` (the human's own API key tier, not a deliberate
  variable) -- the tighter rate limit had starved the translation queue
  badly enough that the "improved" run had only translated ~46% of the
  clip's utterances, making the comparison invalid. This risk is highest
  for experiments run outside this session's own sandbox (a human's local
  machine, a different cloud environment) where config drift (rate
  limits, SDK versions, model names, endpointing values) is easy to miss
  and easy to introduce unintentionally. If a mismatch is found, do not
  silently ignore it or silently "correct for" it with a heuristic --
  state the confound plainly in `result_summary` and treat the run as
  inconclusive for the variable under test, same as an environment
  blocker in `RUN_EXPERIMENTS`.
- Compare the new experiment's metrics (chrF, latency, and, once
  `h-flicker-metric` has landed, normalized erasure) against the relevant
  baseline row(s) in `experiments/results.csv`.
- Write `hypothesis.result_summary` (a few sentences: was the prediction
  right, wrong, or inconclusive, with the actual numbers).
- Set `hypothesis.status = "tested"` (or `"abandoned"` if the implementation
  turned out to be unsound and shouldn't be pursued further -- explain why).
- Advance to `WRITE_REPORT`.

## State: WRITE_REPORT

- Write a **Japanese** report to
  `research_agent/reports/YYYYMMDD_cycle<N>_report.md` covering: what was
  searched/read this cycle, which hypotheses were generated/approved/
  rejected and why, what was actually run, and the results in plain
  language a non-specialist can follow. This satisfies the standing
  instruction to update results reports in Japanese periodically -- do
  this at least once per cycle (every time you pass through this state),
  not just occasionally.
- Also touch `research_agent/reports/00_pipeline_overview_ja.md` if the
  pipeline's shape has materially changed since it was last written (new
  states, changed budget policy, etc.) -- that file is the standing
  Japanese explainer of the *pipeline itself* (architecture), separate
  from the per-cycle *results* reports.
- Commit.
- Advance to `REFLECT`.

## State: REFLECT (self-improvement)

This is the self-improvement/self-reflection step. Append a dated entry to
`research_agent/state/reflections.md` (English is fine here -- it's your own
working memory, not a human-facing report) answering honestly:

1. What worked this cycle? What didn't?
2. Was the hypothesis backlog well-calibrated (too ambitious / too timid /
   duplicating past work)? Should search queries change next cycle?
3. Is the auto-approval budget policy still right, or should you propose
   (to the human, via the next report -- do not silently change
   `budget.json`'s caps yourself) a change?
4. **Should this playbook itself change?** If you find yourself repeatedly
   working around an unclear or wrong instruction in this file, EDIT THIS
   FILE to fix it for next time, and note what you changed and why in
   `reflections.md`. This file is allowed to evolve -- that is the point
   of the self-improvement step. Do not remove the approval-gate or
   budget-check steps themselves without an explicit human instruction to
   do so.
- Advance to `SEARCH_PAPERS` (new cycle, `cycle` counter auto-increments)
  or directly to `GENERATE_HYPOTHESES` if the existing paper base already
  clearly supports more untested hypotheses and a fresh search isn't
  likely to add much this cycle.
- Commit.

## Hard rules (do not relax these without an explicit human instruction)

- **Never** download new YouTube content or use API budget beyond what
  `orchestrator.py check-budget` auto-approves without going through
  `WAITING_APPROVAL` and getting a human signal first.
- **Never** skip `log-cost` after an experiment that used real API calls.
- **Commit often.** After every state transition, and after every file you
  write (state JSON, papers, hypotheses, reports, code), run `git add` on
  the specific paths and commit with a short message. This repo's
  instructions (`CLAUDE.md`) already require committing experiment
  artifacts; treat pipeline state the same way so a session that gets cut
  off mid-cycle can always be resumed from the last commit.
- Follow this repo's existing experiment-logging convention in `CLAUDE.md`
  for every experiment run (JSON + results.csv row + commit) in addition
  to this playbook's own bookkeeping.
- If `uv run ruff check .` fails on code you wrote, fix it before
  committing.

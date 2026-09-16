# Research Agent Reflections (self-improvement log)

Append-only. One dated entry per pass through the REFLECT state. English
is fine here -- this is internal working memory for future sessions of
this same agent, not a human-facing report (see `research_agent/reports/`
for the Japanese human-facing reports).

---

## Cycle 1 (2026-09-08)

**What worked:** Picking a $0, retroactive-analysis hypothesis
(h-flicker-metric) first was the right call -- it needed no approval
back-and-forth, produced a genuinely new and interesting finding
(translation layer is stable, ASR-interim layer is not, ~0.20 NE), and
gave the next two queued hypotheses (masking, LocalAgreement) a concrete
baseline to beat instead of testing blind. Recommend future cycles keep
prioritizing $0/no-new-data hypotheses before spending any of the daily
budget, per PLAYBOOK's existing guidance -- this cycle confirms that
guidance was right, not just cautious.

**What didn't work / gaps:** The literature search this cycle was a single
WebSearch query, not a real EXTRACT_PAPERS pass -- the 3 new papers found
(AlignAtt4LLM, Google stability blog, NeMo@IWSLT2026) are still snippet-only
stubs in papers.json with empty key_findings. Next cycle's SEARCH_PAPERS/
EXTRACT_PAPERS should WebFetch AlignAtt4LLM (arXiv:2606.03967) properly
before finalizing the h-localagreement-asr-commit experiment design, since
that paper's commit rule may differ from plain LocalAgreement-2 in a way
that matters (it's decoder-only-LLM-specific, which is closer to this
repo's setup than the original whisper-streaming paper).

**Backlog calibration:** 3 hypotheses, 1 tested this cycle, 2 queued for
next -- reasonable, not over-ambitious. Don't add more until at least one
of the 2 queued ones is tested; PLAYBOOK's "prefer depth over breadth,
cap backlog at ~6" rule is fine as-is, no change needed.

**Budget policy:** No spend yet ($0 of $7 daily cap used). Too early to
tell if $7/day is well-calibrated -- revisit after h-masking-holdback and
h-localagreement-asr-commit actually run (estimated $1.5 each). No
proposal to change it yet.

**Playbook changes made this cycle:** None needed -- the state machine and
approval flow worked as designed. One thing worth flagging for a human
(not changing unilaterally): the orchestrator.py note strings should avoid
`$` characters when passed through a shell (a note with a literal cost
figure got shell-mangled into a `/bin/zsh` path during this cycle's
HUMAN_APPROVAL transition -- harmless since it's just a free-text note
field, but worth using single-quoted heredocs or the --note flag with
plain text only, no `$0`-style placeholders, in future shell invocations).

---

## Cycle 2 (2026-09-08)

**What worked:** Extracting the 3 stubbed papers via WebSearch (since
WebFetch was EGRESS_BLOCKED for arxiv.org/research.google/aclanthology.org
in this sandbox) still produced substantive, specific findings, not
marketing fluff -- the AlignAtt4LLM read in particular caught a real
design error before any code was written: the original hypothesis draft
implicitly assumed AlignAtt4LLM's commit rule was LocalAgreement-style,
but it's actually attention-internals-based and inapplicable to an
API-only translator. Catching that during READ_PAPERS/GENERATE_HYPOTHESES
(cheap, no API spend) instead of after implementing and running an
AlignAtt-style experiment (expensive, and impossible anyway) validates
the playbook's ordering of literature-then-hypotheses.

**What didn't work / gaps:** Assumed "DEEPGRAM_API_KEY set" + "ffmpeg
installed" meant an experiment could actually run, and only discovered
otherwise after implementing the masking-holdback code and attempting a
live run -- the Deepgram websocket handshake failed with a 403 that
turned out to be the sandbox's network egress proxy rejecting
api.deepgram.com outright (org policy), while generativelanguage.
googleapis.com (Gemini) was fine. This should have been caught earlier
with a direct `curl` connectivity check instead of just checking whether
env vars were non-empty. Fixed the playbook itself (RUN_EXPERIMENTS
section now curls both API hosts directly) so a future session catches
this in the first orientation step instead of after writing code.

**Backlog calibration:** 4 hypotheses now (1 tested, 2 blocked-on-infra,
1 new $0.3 workaround). Still under the ~6 cap. The new
h-gemini-only-masking-replay hypothesis is deliberately scoped to route
around the newly-discovered Deepgram block rather than just waiting for
a human to fix the proxy -- felt like the right call given the playbook's
"never fabricate results, but don't just stall either" spirit. Next
cycle should actually implement it rather than adding yet more hypotheses
on top -- backlog depth over breadth still applies.

**Budget policy:** Still $0 spent (both real runs this cycle failed at
the Deepgram connection step, before any billable Deepgram audio was
sent; Gemini was never called since the pipeline never got past
`transcriber.connect()`). No proposal to change the budget policy --
haven't actually spent anything against it yet to have an opinion.

**Playbook changes made this cycle:**
1. RUN_EXPERIMENTS's environment check now curls both `api.deepgram.com`
   and `generativelanguage.googleapis.com` directly instead of only
   checking whether the env vars are non-empty, since a set key and a
   reachable API turned out to be two different things in this sandbox.
2. Noted that a missing `ffmpeg` is often fixable in-session via
   `apt-get install ffmpeg` (worked cleanly this cycle) rather than being
   an automatic hard blocker.
3. Updated 00_pipeline_overview_ja.md section 7 to reflect that
   hypothesis-required code changes (env-gated experimental toggles) are
   normal, and to document the asymmetric-egress-proxy gotcha for anyone
   reading the pipeline overview (not just PLAYBOOK.md).

Did not touch the approval-gate or budget-check steps themselves.

## Cycle 3 (2026-09-08, later session)

**What worked:** Verifying the environment fresh rather than trusting
cycle 2's blocked_reason at face value paid off -- the sandbox's network
state had genuinely changed (Deepgram REST reachable, ffmpeg installed
cleanly again) within the same day, across sessions. Re-checking instead
of assuming "still blocked" is the right default for anything
environment-dependent. Also good: reading `pipeline.py`'s
`_stream_batch`/`_translation_worker` in detail *before* writing
`replay_masking.py`, which surfaced that `flicker_metrics.py`'s
translation-NE grouping is per-batch, not per-utterance, and would not
have actually measured what h-masking-holdback is meant to fix
(cross-continuation retranslation drift). Stopping short of implementing
against a metric known to be wrong, instead of rushing a "looks done"
result, matches the playbook's "never fabricate results" spirit even
though this wasn't literally a fabrication risk -- it would have been a
*real* experiment measuring the *wrong thing* and reporting a false
negative, which is arguably worse because it looks legitimate.

**What didn't work / gaps:** Assumed a 200 on Deepgram's REST endpoint
meant streaming would work too -- wrong again, in a new way (cycle 2 was
REST+WS both blocked at the proxy CONNECT level; cycle 3 is REST fine,
WS-upgrade specifically 403). Two cycles in a row where an environment
check that looked sufficient turned out not to be. Fixed by adding an
actual WS-connect test to PLAYBOOK.md's RUN_EXPERIMENTS section this
cycle -- if a future session hits the same thing a third time, that's a
sign to look harder at *why* (proxy WS-upgrade policy vs. a Deepgram
key/plan scope difference) rather than just re-documenting the symptom
again. Separately, `uv sync`/`uv run` failing on an unrelated `zoom`
extra (testpypi's `rtms`, blocked by the proxy) cost real time to
diagnose -- worth having caught in cycle 1 or 2 already since experiments
never need that extra; documented the `uv pip install -e ".[experiments]"`
workaround now so it's a non-issue going forward.

**Backlog calibration:** Still 4 hypotheses, unchanged this cycle
(GENERATE_HYPOTHESES was correctly a no-op per the cycle-2 plan). Next
cycle's GENERATE_HYPOTHESES should consider adding a small, scoped
hypothesis for cross-utterance/cross-continuation translation NE
(diffing successive full-utterance retranslations directly, per the
note on h-gemini-only-masking-replay) -- this is arguably higher-value
than either blocked live-ASR hypothesis right now, since it's a $0
retroactive analysis (like h-flicker-metric was) that doesn't depend on
Deepgram at all and fixes a real gap in already-tested h-flicker-metric's
methodology.

**Budget policy:** Still $0 spent, three cycles running. No proposal to
change caps -- there's simply been no successful billable run yet to
have data-driven grounds to revisit them. Not concerning yet, but if
cycle 4 also fails to spend anything, worth flagging in the report to
the human as a "the auto-approved backlog has been stuck on
infra for three cycles" signal rather than silently repeating the same
loop.

**Playbook changes made this cycle:**
1. RUN_EXPERIMENTS's environment check now also tests the actual
   Deepgram listen-websocket directly (Python + `websockets`), not just
   the REST `/v1/projects` endpoint -- REST-reachable was proven
   insufficient evidence twice now.
2. Documented the `uv sync`/`uv run` all-extras-lock problem (pulls in
   the unreachable `zoom`/`rtms` testpypi dependency even for a plain
   experiment run) and the `uv pip install -e ".[experiments]"`
   workaround.
3. Updated `00_pipeline_overview_ja.md` section 7 with both of the above
   for the human-facing pipeline explainer.

Did not touch the approval-gate or budget-check steps themselves.

## Cycle 4 (2026-09-08, later session, scheduled/automated run)

**What worked:** Following through on cycle 3's own plan
(h-cross-utterance-flicker was flagged there as the recommended next
hypothesis) paid off immediately -- it needed no live API access at all,
so it was runnable regardless of whether the Deepgram infra blocker had
recurred this session (it wasn't even re-checked at the WS level this
cycle, deliberately, since it wasn't needed). The result was also a
genuine, non-trivial finding, not a null result: cross-batch translation
NE (~1.47 mean, 275 multi-batch spans out of 4471 total) is three to
four orders of magnitude higher than the within-batch figure
h-flicker-metric reported in cycle 1, and manual inspection of raw
events (the smoketest file) confirmed it's real -- successive batches
for the same utterance produce unrelated Japanese wording, not a shared
prefix. Also worked: sanity-checking a surprising aggregate number (NE >
1, which isn't intuitive) against the underlying raw event text before
writing it into the human-facing report, rather than trusting the
aggregate alone.

**What didn't work / gaps:** `uv run` still fails on the unrelated `zoom`
extra even after cycle 3 documented the workaround -- worth being more
precise next time: `uv pip install -e ".[experiments]"` avoids it at
*install* time, but `uv run <console-script>` re-triggers a sync anyway.
The actual fix used this cycle was calling
`python3 -m real_time_translation.experiments.flicker_metrics` directly
via the activated venv, bypassing `uv run` entirely for pure-analysis
scripts that need no live API access. Documenting this precisely in
PLAYBOOK.md now (see change #1 below) so a future session doesn't
rediscover the same "workaround didn't actually work" gap. Also: did not
re-verify the Deepgram Listen WebSocket or install ffmpeg this cycle,
since the chosen hypothesis needed neither -- that's a deliberate scope
choice, not an oversight, but it does mean cycle 5 starts with the
live-ASR environment status unknown again and should re-check before
picking among the three still-blocked hypotheses.

**Backlog calibration:** 5 queued/proposed hypotheses now (under the cap
of 6), one newly tested this cycle. This cycle's result also changes the
calculus for the backlog: h-masking-holdback and
h-localagreement-asr-commit are no longer just "blocked on infra" --
they now target a confirmed, sizeable problem (not a hypothetical one),
which raises their priority once Deepgram access is restored. Also
worth flagging: the survey-derived literature base (papers.json) has not
had a fresh WebSearch pass since cycle 1 (cycles 2-4 all reused or
skipped search, per each cycle's own REFLECT decision) -- three cycles
running now. That was a reasonable call each individual time (there was
always a clearer, more valuable non-search action available), but
"reasonable every time" can still add up to "the search step has quietly
atrophied." Recommending cycle 5 actually run a fresh SEARCH_PAPERS pass
(targeting: multi-batch/continuation retranslation stability policies
specifically, given this cycle's finding) rather than deferring again by
default.

**Budget policy:** Still $0 total spent, four cycles running, entirely
because every hypothesis actually executed so far has been a $0
retroactive analysis (by design -- PLAYBOOK.md prioritizes these) while
the three hypotheses that need real spend remain blocked on Deepgram
Listen-WebSocket access. This is worth surfacing to the human plainly
(also stated in this cycle's Japanese report): the auto-approval budget
policy itself has never actually been exercised end-to-end. No proposed
change to the caps themselves -- there's still no data suggesting they're
wrong, just no evidence yet that they're right either.

**Playbook changes made this cycle:**
1. Will document the `uv run` vs `python3 -m <module>` distinction for
   pure-analysis scripts (`uv run` still re-triggers the zoom-extra sync
   even inside an installed `.venv`; use `python3 -m
   real_time_translation.experiments.<script>` directly instead) --
   adding this to PLAYBOOK.md's RUN_EXPERIMENTS environment-setup note
   now.

**Next state:** Advancing to `SEARCH_PAPERS` (new cycle) rather than
`GENERATE_HYPOTHESES` directly, per the backlog-calibration note above --
it's been three cycles since the last real search and this cycle's
finding (large cross-batch retranslation drift) gives a concrete new
angle to search for (streaming MT continuation/re-translation stability
policies) rather than repeating the cycle-1 query blindly.

## Cycle 5 (2026-09-09)

**What worked:** Following through on cycle-4's plan to run a real
SEARCH_PAPERS pass (rather than deferring again) paid off -- one of the
4 new papers (`zoom2025-self-speculative-retranslation`) independently
named and grounded exactly the mechanism `h-gemini-only-masking-replay`
was already informally built on ("display-only masking"), and the other
3 all converged on the same conclusion already reached for AlignAtt4LLM
in cycle 2 (white-box attention access needed, not applicable to this
repo's API-only translator) -- useful confirmation, not wasted effort,
and correctly resulted in a GENERATE_HYPOTHESES no-op rather than padding
the backlog for its own sake.

The most valuable thing this cycle did was going a level deeper on two
"blocked" hypotheses instead of accepting the blocked status at face
value:
1. The Deepgram WS blocker (known since cycle 3 as a generic 403) is now
   root-caused precisely: the sandbox's egress proxy TLS-terminates
   outbound HTTPS and mangles the WebSocket upgrade handshake headers
   (Deepgram's own error, once an unrelated OpenSSL CA-cert-strictness
   issue is worked around, is literally "Connection header did not
   include 'upgrade'"). This is now specific enough that a human fixing
   the proxy config knows exactly what to allow.
2. `h-gemini-only-masking-replay` looked like the "easy" $0 hypothesis
   (no Deepgram needed) but attempting to actually design
   replay_masking.py surfaced that its core premise doesn't hold: the
   per-batch source text masking-holdback needs for continuation batches
   was never logged anywhere in the existing 47 experiment JSONs. This is
   exactly the kind of thing PLAYBOOK.md's RUN_EXPERIMENTS section warns
   about ("building the replay harness against the wrong metric would
   produce a misleading result, worse than not running it") -- caught
   before writing a single line of the harness itself, not after.

**What didn't work / lesson:** All 3 queued hypotheses are still
untested after 5 cycles. This isn't churn -- each blocker found this
cycle was real and specific -- but it does mean the backlog is now
uniformly blocked on one external thing (the proxy's WebSocket handling)
that this session has no ability to fix directly. Cost/benefit of
continuing to poke at the same blocker every cycle is diminishing: cycle
3 found "403", cycle 5 found the precise HTTP-layer reason, but neither
session can act on that finding themselves. The self-fix I *could* make
(TimedEvent.original_text) doesn't unblock anything until a live
recording succeeds anyway.

**Backlog calibration:** Still well-calibrated (5 items, cap 6,
depth-over-breadth respected). No new hypotheses added this cycle, and
that was the right call -- more ideas isn't the bottleneck, execution is.

**Budget policy:** Unchanged recommendation from cycle 4 -- still $0
total spent across 5 cycles, still no evidence either way on whether the
$3/$7 caps are right, because nothing has actually spent against them
yet. Not proposing a change.

**Playbook changes made this cycle:**
1. Documented (in RUN_EXPERIMENTS) that `ruff` is not pulled in by
   `.[experiments]` -- `uv run ruff check .` fails via the zoom/rtms sync
   issue as always, but even `python3 -m ruff` fails with "No module
   named ruff" unless `uv pip install ruff` is run once in the venv
   first. Adding this as an explicit extra step next to the existing
   venv-setup note.
2. Extended the inline Deepgram WS connectivity check script: the
   existing version only catches `ssl.SSLError` from
   `load_verify_locations()`, not from the actual TLS handshake during
   `connect()` -- this cycle hit a `SSLCertVerificationError` at handshake
   time ("CA cert does not include key usage extension", an OpenSSL 3.x
   strictness quirk with the proxy's injected CA) that the old script
   would have reported as a bare, unhelpful traceback. The new version
   catches that specifically, retries with cert verification disabled to
   distinguish "our TLS trust setup is broken" from "Deepgram/the proxy
   itself is rejecting us", and reports both outcomes distinctly (a
   `websockets.exceptions.InvalidStatus` after that retry, with its
   response body, is the real signal -- e.g. this cycle's "Connection
   header did not include 'upgrade'").

**Next state:** Advancing to `GENERATE_HYPOTHESES` for cycle 6 rather
than `SEARCH_PAPERS` -- the literature base is fresh (searched this
cycle), and there's no new angle a search would add right now that isn't
already covered by the existing backlog. GENERATE_HYPOTHESES should stay
a no-op again unless the WS proxy issue is resolved by then (in which
case, jump straight to running `h-masking-holdback`, which is fully
implemented and ready) or a genuinely new $0-cost, no-live-API angle
occurs to that session (e.g. the cycle-4-flagged time-to-second-batch
distribution analysis).

## Cycle 6 (2026-09-10, scheduled/automated run)

**What worked this cycle:** Following cycle 5's own explicit fallback
plan paid off directly -- it named a concrete candidate ("the cycle-4
time-to-second-batch distribution analysis") for exactly the situation
that occurred (WS proxy still blocked), so this session didn't have to
improvise; it just executed the plan. `h-utterance-batch-timing` was
cheap to implement (one small extension to an existing function),
cheap to verify (corpus-wide NE numbers unchanged confirmed the change
was purely additive), and produced a genuinely useful, non-obvious
result: the first-batch-duration distribution for multi-batch spans
(mean 3.76s, median 3.46s, min 2.43s) lines up almost exactly with
`config.py`'s `deepgram_max_interim_duration=2.5s`. That's a concrete,
falsifiable link between an already-known config constant and an
already-known NE finding that neither h-cross-utterance-flicker nor any
prior cycle had drawn explicitly.

**What didn't work / lesson:** Environment re-verification (ffmpeg
reinstall, fresh venv, WS handshake test) is now costing a very similar
amount of session time each cycle for an unchanging result -- 4
consecutive cycles (3, 4 skipped it, 5, 6) have found the identical
proxy WS-upgrade-mangling signature. It's still correct per the
playbook to re-check every cycle (an environment fix could land at any
time, silently, from outside this session), but the diagnostic script
itself could be made faster to run without losing rigor -- e.g. skip
rebuilding the venv from scratch if `.venv` already has `websockets`
importable, only reinstalling when the check actually fails. Not
changing this yet since venv state doesn't persist between sessions in
this sandbox anyway (each cycle starts fresh), so the potential savings
may be moot in practice -- flagging for a future cycle to confirm
whether that assumption holds.

**Backlog calibration:** Still well-calibrated (4 tested + 3 blocked =
mix, cap 6). One new hypothesis added this cycle, which was the right
call -- it was cheap, concrete, and answered a real open question
flagged by prior work, not backlog padding for its own sake.

**Budget policy:** Unchanged recommendation -- still $0 total spent
across 6 cycles. Nothing new to propose; the caps remain untested by
actual spend.

**Playbook changes made this cycle:** None. The documented workarounds
(venv setup, ruff install, WS diagnostic script) all worked as written
with no surprises this cycle -- first cycle in a while where the
playbook itself needed no edits.

**Next state:** Advancing to `SEARCH_PAPERS` for cycle 7 rather than
`GENERATE_HYPOTHESES` directly. Reasoning: the $0-cost, no-live-API
retroactive analyses on the existing 47-file corpus are now largely
exhausted (NE within-batch, NE cross-batch, and now cross-batch timing
have all been measured) -- a `GENERATE_HYPOTHESES` no-op would just
repeat what cycle 6 already did, whereas a fresh WebSearch pass (last
one was cycle 5, one cycle ago, but this session found no further $0
angle on its own) gives the next session new material to ground
hypotheses in, or to confirm there's genuinely nothing more to search
for. If the WS proxy issue is resolved by then, skip the search and go
straight to running `h-masking-holdback` (fully implemented, ready to
go) ahead of everything else -- that always takes priority over more
literature review.

## Cycle 7 (2026-09-10, scheduled/automated run)

**What worked this cycle:** A fresh WebSearch pass (last one was cycle
5) was worth it this time, unlike some prior "confirm and no-op" search
attempts -- it found two independent papers (hoang2026-dynamic-lagging,
koshkin2024-tollmatch-zeroshot-context-aware) converging on the same
concrete idea (carry the utterance's own already-emitted translation
forward into continuation-batch prompts), and cross-referencing that
idea against this repo's own `pipeline.py` (not just against
`experiments/results.csv`) turned up a genuine, previously-undocumented
code gap: `_stream_batch()` retranslates continuation batches from
scratch with zero anchor to the utterance's own prior output, and
`commit_context()` only fires once per whole utterance. This is a
stronger form of literature grounding than most prior cycles achieved --
the hypothesis isn't just "a paper suggests X", it's "a paper suggests X,
and reading our own code confirms we are not doing X". Recommend future
SEARCH_PAPERS/READ_PAPERS cycles keep doing this: always check a
promising paper's idea against the actual pipeline code, not just
against the experiment results CSV.

**What didn't work / lesson:** Same environment story as cycles 3, 5,
and 6 -- ffmpeg missing, fresh venv needed, WS handshake still fails
with the identical HTTP 400 "Connection header did not include
'upgrade'" signature. This is now 4 cycles (3, 5, 6, 7) with byte-for-byte
identical proxy behavior. The playbook's guidance to re-verify every
cycle is still correct in principle (the fix could land invisibly at any
time), but there is no longer much diagnostic value in re-running the
*full* diagnostic script every cycle now that the failure mode is this
well-established -- a future session could reasonably do a fast check
(REST curl + a single WS connection attempt) and only fall back to the
full cert-verify-vs-disabled diagnostic if something about the failure
*changes* from this exact signature. Not editing PLAYBOOK.md to shorten
the check yet, since the full diagnostic is what caught the *previous*
change in failure mode (cycle 3's generic 403 becoming cycle 5's more
specific TLS/HTTP 400 split) -- shortening it risks missing a similarly
subtle future change. Flagging as a judgment call for whoever next finds
the environment still blocked after several more identical cycles.

**Backlog calibration:** Still well-calibrated (3 tested + 4 queued/all
blocked = 7 total, backlog of 4 under the cap of 6). Adding exactly one
new hypothesis this cycle was right -- it was well-grounded (two
independent papers plus direct code confirmation) and answers a
concrete, previously-unaddressed question, not padding.

**Budget policy:** Unchanged recommendation -- still $0 total spent
across 7 cycles. The auto-approval caps ($3/batch, $7/day) remain
completely untested by actual spend, since every hypothesis needing live
API calls has been blocked by the same infra issue since cycle 3. If/when
Deepgram access is restored, the very first live run will be the first
real test of whether these caps are sized reasonably for this repo's
actual per-experiment cost -- worth watching closely on that first run
rather than assuming the caps are fine.

**Playbook changes made this cycle:** None needed. All documented
workarounds (ffmpeg via apt-get, python3.13 venv + `uv pip install -e
".[experiments]"`, the WS diagnostic script) worked exactly as written.

**Next state:** Advancing to `GENERATE_HYPOTHESES` for cycle 8 rather
than `SEARCH_PAPERS` again -- the literature base is fresh (searched
this cycle) and yielded a concrete new hypothesis already; another
search immediately next cycle is unlikely to add much. If the WS proxy
issue is resolved by then, skip straight to running `h-masking-holdback`
(fully implemented, ready to go) ahead of everything else -- that always
takes priority over more hypothesis generation. If it's still blocked,
consider starting the `h-continuation-context-anchor` implementation
(the `llm_translator.py` prompt extension and `pipeline.py` per-utterance
translated-text tracking) even without being able to run it yet, so it's
ready to fire the moment Deepgram access returns -- this is the same
"implement now, run later" pattern that worked well for
`h-masking-holdback` back in cycle 2.

## Cycle 8 (2026-09-11)

**What worked:** Started cleanly from `HUMAN_APPROVAL` per cycle 7's
handoff. Confirming that no hypothesis had `approval` unset/`"proposed"`
before doing anything else made this state a fast, honest no-op instead
of redundant busywork. Re-verifying the Deepgram listen-websocket with
the full diagnostic (not a shortcut) again paid off as a sanity check --
same exact HTTP 400 "Connection header did not include 'upgrade'"
signature as cycles 5, 6, 7, so still no silent regression/change to
miss.

**What didn't work / new finding:** Independently re-derived (before
re-reading cycle 5's note) that `h-gemini-only-masking-replay` is stuck
on the same root cause as the other 3 queued hypotheses, just one layer
removed: all 47 existing experiment JSONs predate the `original_text`
field (added 2026-09-09) and none has a `date >= 2026-09-08`, so there is
no per-batch source text anywhere to replay. Checked whether
`asr_interim` event timestamps could substitute -- only 14/42 batch keys
matched exactly on a sample file, because translation-batch keys are the
*new fragment's* own start/end time while `asr_interim` keys are
Deepgram's utterance-relative interim timing. Decided against building an
approximate-timestamp-join replay: feeding the real `LLMTranslator` an
approximated (not actually recorded) source text and presenting the
output as testing masking-holdback "on real Gemini output" would be
misleading about what was actually tested, even though every individual
LLM call would be real. This is exactly the kind of thing the "never
fabricate results" rule should also cover on the *input* side, not just
the output side -- worth being explicit about that distinction if this
comes up again for another hypothesis.

**Backlog calibration:** Still well-calibrated (3 tested + 4
queued/all blocked = 7 total). Did not add a new hypothesis this cycle
-- there wasn't a new literature-grounded idea to add, and the backlog
is already all we can act on once Deepgram access returns; adding a 5th
blocked hypothesis right now would just be padding, not depth.

**Budget policy:** Unchanged recommendation, still $0 spent across 8
cycles. No new information to revise the $3/batch, $7/day caps -- still
completely untested by real spend.

**Playbook changes made this cycle:** Added a short note to the
RUN_EXPERIMENTS ffmpeg-install step: this cycle's `apt-get install`
aborted on unrelated mirror failures (libva2/libssh-gcrypt-4/libcaca0)
even though ffmpeg's own package had already downloaded, and a
`--fix-missing` retry did not resolve it within the session. Documented
that this is not reliably one-shot and that a future session shouldn't
loop on it indefinitely -- cap it at ~2 attempts and move on, especially
since it's moot whenever the Deepgram websocket check is also failing.

**Next state:** Advancing to `SEARCH_PAPERS` for cycle 9. Reasoning:
cycle 7 already did a fresh search and cycle 8 didn't add any literature
work, so the literature base is one cycle less fresh than it was; more
importantly, the backlog is now fully saturated with blocked hypotheses
(4/6) and no new $0 retroactive angle has surfaced in two cycles, so the
highest-value use of a fresh search is to look specifically for (a) any
new SimulST work with an evaluation methodology that doesn't require
live ASR access (in case there's a smarter way to make progress while
Deepgram stays blocked than the ones already tried), and (b) anything
concrete on prompt-based continuation-context anchoring to sharpen
`h-continuation-context-anchor`'s design before it's implemented.

## Cycle 9 (2026-09-12)

**What worked:** The targeted search paid off exactly as planned --
both cycle-8-flagged gaps got a concrete hit
(machacek-polak2025-cuni-offline-cla-latency for the no-live-ASR-needed
evaluation gap, beavertalk2025-sentence-memory-bank for the
context-anchor-prompting gap), and the CLA paper's own metric turned out
to be directly implementable against this repo's own data (a genuine
gold VTT transcript already sitting in experiments/refs/, covering 19 of
47 existing experiments). Went all the way through a full cycle
(SEARCH_PAPERS -> ... -> REFLECT) in one session since each state's unit
of work stayed genuinely bounded and each was committed before moving
on -- this confirms last cycle's own note that the playbook's "you may
continue into the next state" allowance is usable in practice, not just
theoretical.

**What didn't work / had to be fixed mid-stream:** Implementing CLA was
not a clean transcription of the paper's method -- three real problems
surfaced only once actual data was run through it: (1) some experiments'
recorded events stop well short of their requested duration_seconds, so
windowing by the *requested* duration rather than the *actual* max
asr_end_time would have silently produced a much larger (and wrong)
gold window; (2) `playback_offset` turned out not to be on the same
clock as the gold transcript for these specific runs (they weren't
strictly real-time-paced), which would have produced a nonsense
"latency" (drifting more negative every group) if not caught by
sanity-checking the first few groups' output by hand instead of trusting
the aggregate mean; (3) the naive version (align every experiment
against one fixed gold transcript with no relevance check) is not just
wrong but dangerous -- it produced a 1.5-billion-cell alignment request
against an unrelated video's experiment and got the whole process
OOM-killed. None of these would have been caught by only reading the
primary source's abstract/methodology description; all three needed
actually running the code against this repo's real data and inspecting
intermediate output by hand (printing per-group latency for the first
~15 groups, not just trusting the final mean) before trusting the
aggregate number. Lesson for future retroactive-analysis hypotheses:
budget time to sanity-check a metric's *intermediate* values on 1-2
files by hand before running it over the full corpus and writing up the
aggregate result as if it were self-evidently correct.

**Backlog calibration:** 5/6 (up from 4/6), still within cap. Was right
to add only 1 new hypothesis (h-cla-asr-latency-metric) rather than all
3 papers' worth of ideas -- BeaverTalk's finding was folded into
strengthening h-continuation-context-anchor's existing design rather
than spawning a separate hypothesis, keeping depth over breadth. The
4 Deepgram-blocked hypotheses are unchanged and still blocked; adding a
6th of the same kind would have been padding, not progress.

**Budget policy:** Unchanged recommendation, still $0 total spend across
9 cycles now. No new information to revise the $3/batch, $7/day caps.

**Playbook changes made this cycle:** None needed -- the ffmpeg
retry-cap note from cycle 8 was followed as written (skipped the
apt-get attempt entirely this cycle since Deepgram was already
confirmed blocked, exactly the "moot" case that note anticipated) and
worked as intended, no friction found.

**Open question worth flagging for a future cycle, not a playbook
change:** this cycle's own `pipeline_state.json["cycle"]` field reads 5,
but every report/commit in this repo's history (including this one) has
been narrating and naming reports by a *different*, larger cycle count
("cycle 9" here) that only matches the number of REFLECT->SEARCH_PAPERS
`orchestrator.py advance` calls, not some cycles apparently having taken
a GENERATE_HYPOTHESES-only shortcut per the state machine's own
"REFLECT -> GENERATE_HYPOTHESES" edge (which doesn't increment the
`cycle` field). This is cosmetic (report filenames and commit messages
are internally consistent with each other, just not with the raw JSON
counter) and not worth an urgent fix, but a future REFLECT should either
reconcile the two counters or stop trying to keep them in sync and just
treat the JSON field as "REFLECT->SEARCH_PAPERS loop count" explicitly
in its own key name.

**Next state:** Advancing to `GENERATE_HYPOTHESES` directly (skipping a
fresh `SEARCH_PAPERS` pass) rather than starting cycle 10 with another
search. Reasoning: this cycle's own ANALYZE_RESULTS surfaced a concrete,
actionable, code-only (not literature-driven) $0 hypothesis candidate --
checking whether `deepgram_endpointing` config actually reaches the
Deepgram connection setup code, motivated by CLA showing a flat ~4.1s
latency across the entire 300-2000ms endpointing sweep where the
existing avg_end_to_end_latency_seconds metric showed large,
non-monotonic swings (7.3-18.9s). That is a question about this repo's
own code, not something a literature search would surface, so the
highest-value next unit of work is to generate and test that hypothesis
directly rather than search first.

---

## Cycle 10 (2026-09-12)

**What worked / what didn't:** This was the cleanest possible unit of
work: cycle 9's own ANALYZE_RESULTS handed cycle 10 a fully-scoped,
concretely-actionable, $0, zero-live-API-dependency question ("does
`deepgram_endpointing` actually reach the connection?"), so
GENERATE_HYPOTHESES this cycle was trivial and RUN_EXPERIMENTS didn't
need to touch the Deepgram/ffmpeg environment at all -- the environment
gap (ffmpeg missing again this session) was genuinely moot for this
hypothesis, which is exactly the kind of case PLAYBOOK.md's cycle-8 note
anticipated. Went one level deeper than a surface code read: downloaded
the pinned `deepgram-sdk` wheel straight from PyPI (unaffected by the
sandbox's Deepgram-specific egress block) to confirm the SDK itself
doesn't silently swallow the `endpointing` kwarg -- this is the kind of
"verify the third-party boundary, not just our own code" step that
past cycles' Deepgram-blocked investigations already modeled well.
Result: the wiring is provably correct, ruling out explanation (b) and
leaving (a) (this clip/threshold-range not exercising the setting, or
CLA measuring a different dimension than endpointing controls) as the
standing account -- a genuine, useful negative result, not a dead end.

**Backlog calibration:** Still well-calibrated (5 of 6 slots used,
4 of those still genuinely blocked on infra, not from a shortage of
ideas). Adding this cycle's hypothesis didn't crowd anything out and
directly resolved the concrete follow-up flagged last cycle rather than
padding the backlog with a tangential idea.

**Budget policy:** Unchanged recommendation. $0 total spend across 10
cycles now (11 including this one's own $0 log entry). No new
information to revise the $3/batch, $7/day caps -- the real bottleneck
remains the Deepgram WS-proxy infra gap, not the budget policy.

**Playbook changes made this cycle:**
1. Fixed a stale hardcoded macOS path in Step 0's orientation snippet
   (`/Users/riki/Desktop/GitHub/real_time_translation`) that does not
   exist in a cloud/scheduled session -- this session's actual working
   copy was at `/home/user/real_time_translation`, on branch
   `feat/rm2278/async-containers` rather than `main` (the pipeline
   files don't exist on `main` at all; a fresh session must `git fetch`/
   `checkout` that branch first, since the repo's default branch alone
   doesn't have `research_agent/`). Replaced the hardcoded path with
   guidance to orient dynamically instead.
2. Resolved the cycle-9-flagged open question about the two mismatched
   "cycle" counters (JSON `pipeline_state.json["cycle"]` vs. the larger
   number narrated in report filenames/commits): documented explicitly
   in Step 0 that the JSON field only counts `REFLECT -> SEARCH_PAPERS`
   loops (per orchestrator.py's `TRANSITIONS`/increment logic), while
   the narrated "cycle N" used in filenames and commit messages
   increments on every REFLECT regardless of target state, and that the
   narrated count is the one to keep using for anything human-facing.
   This was flagged as "not urgent" in cycle 9 but the fix was cheap
   enough to just make now rather than let a future session re-derive
   the same explanation a third time.

**Next state:** Advancing to `GENERATE_HYPOTHESES` again (skipping a
fresh `SEARCH_PAPERS` pass) for cycle 11, since h-endpointing-connection-
verify's own result_summary flags a concrete, testable next question
that doesn't need new literature: directly measuring `is_final`/
`UtteranceEnd` event timing (not just settled-text stability) across the
existing endpointing-sweep experiment JSONs, to properly settle
explanation (a) left open this cycle. If the Deepgram WS-proxy issue is
resolved by the time cycle 11 runs, prioritize running `h-masking-holdback`
live immediately instead (code has been ready since cycle 2).

## Cycle 11 (2026-09-13, scheduled/automated run)

**What worked:** Generated `h-asr-final-emission-latency` as a direct,
concrete follow-up to cycle 10's own flagged open question (explanation
(a) vs (b) for the flat CLA-latency result) instead of a fresh literature
search -- this kept the cycle focused and, like cycle 5's
`h-gemini-only-masking-replay` finding, the *implementation* itself
surfaced a real correction to the hypothesis's premise before any
misleading result could be produced: there is no distinct `asr_final`
event kind in the logs, and `TimedEvent.is_final` for translation events
means something unrelated (translation-call completion, not ASR
finality). Caught this by actually inspecting real experiment JSON data
(counting event kinds) before writing the analysis script, not just by
re-reading the dataclass docstring -- the docstring alone would not have
revealed that 0 events ever have `kind="asr_final"`. The real signal
(`is_utterance_end`, distinguishing Deepgram-native finalize from the
`max_interim_duration` soft-finalize timer) was already sitting in
`deepgram_client.py`'s own docstrings, previously read by
`h-cross-utterance-flicker`/`h-utterance-batch-timing` for unrelated
purposes -- worth remembering that a field logged for one purpose can
answer a completely different question later.

**What didn't:** Nothing failed this cycle in the sense of wasted API
spend or a broken commit, but the *first* draft of the hypothesis
(written during `GENERATE_HYPOTHESES`, before implementation) was wrong
about what data existed. This is the second time in this pipeline's
history (after `h-gemini-only-masking-replay` in cycle 5) that a
hypothesis's premise about the event-log schema needed correcting once
someone actually tried to build against it. A cheap process improvement
for next time: before finalizing a new hypothesis's `required_changes` in
`GENERATE_HYPOTHESES`, do one quick `python3 -c "..."` spot-check of an
actual experiment JSON's event `kind`/field values referenced in the
description, rather than relying on dataclass comments alone -- comments
can describe intent or a past state that no longer matches the data.

**Backlog calibration:** Good this cycle -- one hypothesis added,
implemented, and fully resolved (found -> approved -> run -> analyzed ->
reported) in a single pass, keeping depth over breadth. Backlog
(queued/proposed) is now 4, all four still genuinely blocked on the
Deepgram WS-proxy infra gap (unchanged since cycle 3, not re-verified
this cycle since none of this cycle's chosen hypothesis needed it).
$0-cost retroactive angles against the existing 47-file corpus feel
substantially exhausted after this cycle: within-batch NE, cross-batch
NE, cross-batch timing, CLA word latency, endpointing wiring, and now the
genuine-vs-soft-finalize mix have all been measured. The next $0 idea
that isn't just re-slicing the same 47 files would likely need either (a)
a genuinely new angle from fresh literature, or (b) accepting a live
experiment is required to make further progress on the ASR/translation
substance (h-masking-holdback et al.).

**Budget policy:** Unchanged recommendation, still $0 total spend across
11 cycles. No new information to revise the $3/batch, $7/day caps.

**Should the playbook change?** No changes made this cycle. The existing
guidance (verify environment before picking a hypothesis; don't fabricate
results; correct a hypothesis's premise in place when implementation
reveals it was wrong) already covered this cycle's situation well. The
one soft lesson (spot-check real data before trusting a schema
description) is recorded above for future cycles but didn't feel like it
rose to the level of a playbook rule yet -- if a third hypothesis in a
row turns out to have a wrong schema premise, that would be the trigger
to add an explicit step.

**Next state:** Advancing to `SEARCH_PAPERS` for cycle 12 (cycle counter
increments), per this cycle's own Japanese report's recommendation --
the $0 retroactive-analysis well against the existing corpus is close to
dry, so a fresh literature pass is more likely to add value than another
`GENERATE_HYPOTHESES` no-op. If the Deepgram WS-proxy issue has been
resolved by then, prioritize running `h-masking-holdback` live
immediately instead (code has been ready since cycle 2).

---

## Cycle 12 (2026-09-13)

**What worked:** The literature pass (SEARCH_PAPERS carried over from
cycle 11, EXTRACT_PAPERS + READ_PAPERS this cycle) was efficient and the
"two papers share the word 'masking' but are unrelated to this repo's
API-only architecture" finding is exactly the kind of thing worth writing
down explicitly so a future cycle doesn't re-discover it. The
coval2026 -> Deepgram Flux thread was a genuinely new, concrete lead that
came from reading an industry blog rather than academic literature --
worth remembering that "prefer arXiv/ACL/Semantic Scholar/OpenReview"
(this file's own SEARCH_PAPERS guidance) shouldn't exclude vendor
benchmark blogs entirely when they carry a specific, checkable claim.

**What didn't, and the important part of this cycle:** rm-2278 ran
h-masking-holdback locally and pushed the result -- genuinely valuable,
since this sandbox's Deepgram WS block has held for 10 cycles now. My
first pass at analyzing it (comparing translation_ne_char_cross_batch_mean
0.947 -> 0.502) read that as a ~47% flicker improvement and I nearly
reported it as a confirmed positive result. Only because I went one level
deeper -- diffing the two runs' own `config` blocks -- did I find
gemini_rpm_limit was 9 vs. 60, an unrelated confound (almost certainly the
human's own API key tier) that fully explains the apparent improvement
(the holdback run's translation queue backed up under the tighter rate
limit and only finished translating the first ~46% of the clip's
utterances, so the "improvement" was really "measured over an easier,
truncated subset"). I had already committed the wrong conclusion once
before catching this and had to push a correction commit.

This is the **third** time in this pipeline's history that trusting a
result/schema at face value produced a wrong conclusion that only surfaced
on a second, deeper look (after h-gemini-only-masking-replay's cycle-5
utterance_id assumption and h-asr-final-emission-latency's cycle-11
is_final-field assumption) -- but this time the wrong assumption wasn't
about *this repo's own code/schema*, it was about an *externally-provided
experiment's comparability*. That's a distinct enough failure mode (not
"I misread a dataclass," but "I compared two runs without checking they
only differed in the one variable I meant to test") that per this file's
own cycle-11 note ("if a third hypothesis in a row turns out to have a
wrong premise, that would be the trigger to add an explicit step"), I'm
adding an explicit rule to ANALYZE_RESULTS below rather than just noting
it here again.

**Playbook change made this cycle:** Added a step to `ANALYZE_RESULTS`
requiring a `config`-block diff between the new experiment and its
baseline before trusting any metric comparison, called out specifically
for human-run/externally-provided experiments where the config is even
more likely to silently differ (API tier limits, SDK versions, etc.) than
in a same-sandbox rerun.

**Backlog calibration:** 4 queued/proposed (within the 6 cap), but one of
them (h-gemini-only-masking-replay) is quietly the most valuable thing to
pick up next cycle -- it's $0, doesn't need the blocked Deepgram path at
all, and has been sitting untouched since cycle 5 while attention went to
literature and to hypotheses that turned out blocked. Flagging this
explicitly so REFLECT doesn't just default to "more SEARCH_PAPERS" out of
habit: next cycle should implement it rather than search for more papers,
unless the Deepgram WS block has cleared (in which case
h-masking-holdback-rpm-matched-retest jumps to first priority instead,
since it directly finishes the human-provided-experiment thread from this
cycle).

**Budget policy:** Unchanged recommendation. $0.02 logged this cycle
(a nominal estimate for the human's own local API usage, not this
session's spend) -- still effectively $0 across 12 cycles from this
sandbox's own side.

**Next state:** Advancing to `SEARCH_PAPERS`... reconsidered -- per the
backlog-calibration note just above, `GENERATE_HYPOTHESES` is not the
bottleneck (backlog has room but the *right* next hypothesis to work is
already queued and well-specified: h-gemini-only-masking-replay). Advance
to `SEARCH_PAPERS` anyway for cycle 13, since a fresh literature pass
costs nothing and the last one was productive (coval2026's Flux lead),
but the note above should steer whoever picks up cycle 13's
RUN_EXPERIMENTS toward implementing h-gemini-only-masking-replay rather
than defaulting to another blocked-on-Deepgram hypothesis.

## Cycle 13 (2026-09-15)

**What worked:** The READ_PAPERS -> GENERATE_HYPOTHESES -> HUMAN_APPROVAL ->
RUN_EXPERIMENTS -> ANALYZE_RESULTS -> WRITE_REPORT chain went smoothly this
cycle, all in one session (found a fresh sandbox with DEEPGRAM_API_KEY/
GOOGLE_API_KEY set and ffmpeg absent but not needed, since the hypothesis I
picked -- h-endpointing-pause-vs-sentence-boundary-audit -- was fully
retroactive). The config-diff step added to ANALYZE_RESULTS after the
cycle-12 near-miss worked exactly as intended here: confirmed the 6-way
endpointing sweep only differs in the one variable under test, no confound,
in under a minute.

**What didn't (in a good way):** The hypothesis's own `required_changes`,
as written at GENERATE_HYPOTHESES time, assumed `asr_interim` events'
`is_utterance_end` field would tell me which interim was the "real"
endpointing-triggered final one. Reading `video_segment.py`'s `on_result()`
before implementing anything showed this is wrong: `TimedEvent.is_utterance_end`
defaults to `True` and is only ever explicitly set on
`translation_partial`/`translation_complete` events -- `asr_interim` events
always carry the unpopulated default, so filtering on it there is
meaningless. Caught this *before* writing the analysis script, not after,
by reading the actual construction site rather than trusting the schema
docstring/my own restated assumption. This is now the third time this kind
of thing has happened (h-gemini-only-masking-replay's original_text-empty
discovery in cycle 5; h-cross-utterance-flicker's utterance_id-doesn't-
exist discovery around the same time; now this). Per this file's own
cycle-11 rule ("if a third hypothesis in a row turns out to have a wrong
premise, that would be the trigger to add an explicit step"), I'm adding a
playbook note this cycle (see below) rather than just logging it here
again -- though note this is a *third instance of the same underlying
pattern* (assuming a TimedEvent field is populated uniformly across event
kinds without checking the construction site), not literally three
hypotheses in a row, so I'm treating it as a advisory note rather than a
hard new gate.

**Backlog calibration:** Added exactly 1 new hypothesis this cycle
(deliberately not more -- both `simulu2026` and `prefix2prefix2026` only
grounded already-tested hypotheses, no independent new testable claim).
Backlog is now 2 queued/proposed (well under the 6 cap): the still-blocked
`h-gemini-only-masking-replay`, and nothing else, since this cycle's new
hypothesis was fully executed to `tested` in the same session. Search
queries: no change needed, this cycle's papers were productive.

**Budget policy:** Unchanged recommendation. $0 spent this cycle (pure
retroactive analysis), consistent with prior cycles' actual sandbox spend.

**Playbook change made this cycle:** Added a short note to
`GENERATE_HYPOTHESES` warning that a hypothesis referencing specific
`TimedEvent`/experiment-JSON-event fields should be checked against the
actual construction sites in `video_segment.py`/`youtube_segment.py` (which
fields are populated for which `kind` values) before being finalized, not
assumed uniform across event kinds -- this is the recurring failure mode
behind the three near-misses above.

**Next state:** Advancing to `SEARCH_PAPERS` for cycle 14 -- the backlog is
thin (1 blocked hypothesis) and a fresh literature pass costs nothing and
has been productive each of the last few cycles. If `h-gemini-only-masking-
replay` is still blocked next cycle (same Deepgram WS proxy issue,
unchanged again this cycle), the environment-audit follow-up flagged in
this cycle's report (whether `deepgram_max_interim_duration` is masking the
true effect of `deepgram_endpointing` at longer thresholds -- a $0 code-
reading + possibly a small retroactive check, no live API needed) is a good
candidate to pick up in `GENERATE_HYPOTHESES` if the search pass doesn't
turn up anything more pressing.

## Cycle 14 (2026-09-16)

**What worked:** Ran the full chain EXTRACT_PAPERS -> READ_PAPERS ->
GENERATE_HYPOTHESES -> HUMAN_APPROVAL -> RUN_EXPERIMENTS ->
ANALYZE_RESULTS -> WRITE_REPORT -> REFLECT in one session. `ffmpeg`
installed cleanly this time (no mirror-failure drama). The config-diff
step in ANALYZE_RESULTS again confirmed a clean single-variable
comparison in under a minute -- this check keeps paying for itself.

**New environment finding:** WebFetch was entirely EGRESS_BLOCKED this
session for every domain tried (arxiv.org, aclanthology.org,
semanticscholar.org, awesomepapers.io, pith.science) -- a broader block
than prior cycles' host-specific Deepgram/Gemini API blocks. WebSearch
itself still worked fine and returned usable abstract-level content for
all 3 papers, so EXTRACT_PAPERS proceeded via WebSearch instead of
WebFetch. Added a note to PLAYBOOK.md's EXTRACT_PAPERS section (see
below) since the playbook previously only documented WebFetch as the
extraction tool.

**A near-miss worth naming honestly:** During READ_PAPERS I cross-checked
the new hypothesis's premise ("no existing experiment varies
output-compression prompt instructions") against `experiments/` by
skimming the directory listing and `results.csv`, and initially missed
that `experiments/20260915_budget_translation_{baseline,enabled,tight}
.json` (visible in that same listing, added via `git log` commits
33faed4/c4fd798 the day before) were exactly that: a human-authored,
just-landed feature (`Config.reading_speed_budget_translation`) doing
almost the same thing, WITH an already-measured fidelity cost via a new
`translation_fidelity_judge.py` tool. I only caught this later, during
RUN_EXPERIMENTS, when reading `llm_translator.py` more closely to design
the actual code change -- before running any experiment, so no budget was
wasted, but it was closer than I'd like: eyeballing a file listing is not
the same as actually checking for topical overlap. Redesigned the
hypothesis's experiment around this finding (reused the existing clip,
the existing fidelity judge, and directly engaged with the sibling
finding) rather than plowing ahead with the original, narrower plan --
this produced a much more informative result (a concrete content-drop
example plus a direct comparison point) than the original design would
have. Adding an explicit PLAYBOOK.md note (see below) so future
GENERATE_HYPOTHESES/READ_PAPERS passes grep for topical keywords and
skim recent `git log`, not just the experiment file list, before
asserting "nothing existing covers this."

**Backlog calibration:** Added exactly 1 new hypothesis (depth over
breadth -- the other 2 papers read this cycle need model-retraining
infra not available here). It was fully executed to `tested` in the same
session, so backlog is now 0 queued/proposed (well under the 6 cap) --
plenty of room next cycle. Search queries: no change needed.

**Budget policy:** Unchanged recommendation. $0.01 spent this cycle (a
small Gemini-only replay, zero Deepgram spend).

**Should this playbook change?** Yes, two additions made this cycle:

1. EXTRACT_PAPERS now notes WebSearch as a fallback when WebFetch itself
   is blocked (not just the RUN_EXPERIMENTS host-specific API blocks the
   playbook already documented).
2. READ_PAPERS's cross-reference step now explicitly says to grep
   `experiments/results.csv` notes and filenames for topical keywords and
   skim recent `git log -- experiments/ src/` (not just eyeball the
   directory listing) before asserting a hypothesis is untested -- per
   the near-miss above.

Did NOT touch the approval-gate or budget-check steps.

**Next state:** Advancing to `GENERATE_HYPOTHESES` directly (not a fresh
`SEARCH_PAPERS` pass) -- the backlog is empty and there's a concrete,
well-scoped follow-up already identified in this cycle's report (repeated
sampling on the segment-5-style content-drop question) worth writing up
before doing a new literature search.

## Cycle 15 (2026-09-16)

**What worked:** This was a human-driven session (rm-2278, interactively,
not a cron-woken cloud sandbox), asked to do a bigger-than-usual literature
+ hypothesis pass focused on two specific questions: (1) latency is still
large, what else can be done, and (2) what happened to the
real-time-benchmark-beyond-chrF investigation. Rather than force the
orchestrator's state machine backward through SEARCH_PAPERS/EXTRACT_PAPERS/
READ_PAPERS (not a legal transition from GENERATE_HYPOTHESES per
`TRANSITIONS` in orchestrator.py), did an informal literature refresh
in-place and documented it plainly in papers.json's
`_websearch_note_cycle15` and here, rather than pretending a formal state
walk happened. Worth normalizing: PLAYBOOK.md doesn't currently say what to
do when a human explicitly requests fresh literature mid-cycle outside the
state machine's own cadence -- added a note about this below.

**Environment discovery worth recording:** this session's WebFetch
successfully reached arxiv.org (PDF) and ai.google.dev directly -- every
prior cycle's EGRESS_BLOCKED report for those hosts was specific to the
cron-woken cloud sandbox, not a property of the hosts themselves. A local,
human-driven session has materially better tool access than the scheduled
cloud sessions this playbook was originally written for; worth remembering
next time a session can't tell which kind of environment it's in.

**A genuinely new, clean finding, from actually running the numbers rather
than assuming:** built `mt_latency_decomposition.py` expecting it might
reveal a nontrivial LLM-inference-time cost worth optimizing (e.g. via a
faster "draft" model). Instead it cleanly falsified that framing:
stream_duration (LLM response emission) is ~0.003s median across 6999
batches -- translation inference is not the bottleneck at all, anywhere in
the corpus. This directly killed two candidate hypotheses before they were
ever written up as full proposals (a two-tier draft+refine model idea, and
RLM-Cascade-style response-level speculative decoding) -- both would have
optimized a step that costs ~3ms. Recorded RLM-Cascade in papers.json with
this reasoning rather than silently dropping it, so a future cycle doesn't
rediscover and re-evaluate the same dead end. This is exactly the kind of
"spot-check real data before trusting an assumption" discipline this
playbook has flagged before (TimedEvent field-population near-misses) --
glad it caught something before code was written, not after.

**Backlog calibration:** Added 6 new hypotheses this cycle instead of the
usual 1-3 -- a deliberate exception because the human explicitly asked for
breadth ("hypothesisをたくさん作る") in this session, not the usual
depth-over-breadth cadence. 3 were fully executed same-session ($0
retroactive/research, low risk); backlog is now 3 queued/proposed
(h-max-interim-duration-raise, h-semantic-completeness-gating,
h-monotonic-chunkwise-prompt-enja), still under the 6 cap. Should return to
the normal 1-3/cycle cadence next time unless the human asks for another
breadth pass.

**A judgment call worth naming explicitly:** `h-max-interim-duration-raise`
is fully diagnosed, cheap (~$0.05), and needs no code change -- just an env
var and a rerun of the existing YouTube experiment runner on the cached
clip. `.env` in this repo has real DEEPGRAM_API_KEY/GOOGLE_API_KEY, unlike
prior cloud-sandbox cycles. Chose NOT to source `.env` and run it anyway
under the existing $3/batch auto-approval policy, even though that policy
would technically cover it -- reasoning: the human's own message this
session was framed as "let's start by generating hypotheses," not "run
experiments," and spending real (if small) money + making live external
API calls felt like it crossed from "the kind of autonomous action this
playbook was designed to allow" into "a specific action worth surfacing
and letting the human trigger explicitly," given it was easy to make
instantly ready-to-run instead (documented in the cycle 15 report with the
exact command) and the human is returning to check in soon regardless.
Flagged in `pending_approval.json` rather than silently either running it
or leaving it unmentioned. Not fully confident this was the right call
versus just running it -- if the human's reaction next time is "just run
things like that, don't ask," that should update this playbook's guidance
on what "auto-approved" really means in an interactive (not scheduled)
session.

**Budget policy:** Unchanged recommendation. $0 spent this cycle (all 3
executed hypotheses were pure retroactive analysis / doc research). Total
across all cycles remains $0.11 of a $7/day cap -- still not a real test of
whether the caps are sized right, since no cycle has yet come close to
either cap.

**Should this playbook change?** Yes, one addition: PLAYBOOK.md's
GENERATE_HYPOTHESES section doesn't currently address what to do when a
human interactively asks for a fresh literature pass outside the normal
SEARCH_PAPERS cadence. Added a short note there pointing back to this
cycle's approach (informal refresh, documented in papers.json's websearch
note, no forced state-machine backtrack) so a future session recognizes
this as sanctioned rather than a deviation to avoid.

**Next state:** Advancing to `SEARCH_PAPERS` for cycle 16 -- the backlog's
3 queued/proposed hypotheses all need either human-approved live budget or
a code change (not more literature), but a fresh search pass is cheap and
this cycle's queries were productive, so worth trying once more before
spending a whole cycle just implementing code for the queued items.

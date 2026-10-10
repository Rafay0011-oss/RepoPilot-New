# RepoPilot

> **Plain code finds and checks the evidence. A small local open-weight model reads it and explains it in simple words.**

RepoPilot is an **offline, evidence-first** question-answering prototype for public
GitHub repositories. You paste a repository URL, ask a question, and every claim in
the answer points to a real `file:line` or a real commit. A **deterministic
verifier** — one that never calls an AI model — checks each claim before it is
shown, so a wrong or fabricated citation is caught instead of trusted.

The headline questions it is built to handle:

- **"How does this system handle a large number of users?"** (scaling)
- **"Why is this method (for example, authentication) used here?"** (rationale)

A repository can show *what mechanisms exist*, but it cannot *prove capacity*, and
it cannot always explain *why* a choice was made. RepoPilot says so honestly
instead of guessing.

---

## Table of contents

1. [Problem](#problem)
2. [Solution](#solution)
3. [AI component](#ai-component)
4. [System architecture](#system-architecture)
5. [Setup](#setup)
6. [Usage and demo](#usage-and-demo)
7. [Testing](#testing)
8. [Design decisions and constraints](#design-decisions-and-constraints)
9. [Limitations and future work](#limitations-and-future-work)
10. [What we built vs. what libraries provide](#what-we-built-vs-what-libraries-provide)
11. [Project status](#project-status)
12. [Team](#team)
13. [License](#license)
14. [Acknowledgements](#acknowledgements)

---

## Problem

Reading an unfamiliar codebase to answer *"does this scale?"* or *"why is this
here?"* is slow and error-prone. Large language models will answer those questions
from general knowledge, but they **hallucinate** project-specific facts and cannot
be trusted to cite real code. On a CPU-only laptop, and under a strict
**no-cloud-AI** rule, you also cannot send whole repositories to a hosted model — and
you should not have to.

## Solution

RepoPilot separates *finding and checking facts* from *explaining them*:

1. **Deterministic code gathers evidence.** A catalog of scaling patterns, a
   shallow `git` history reader, a Python `ast` index, and a keyword router collect
   a handful of exact `file:line` snippets (or real commit messages).
2. **A small local model explains that evidence** in plain words and returns
   structured JSON claims, each labelled and citing evidence ids.
3. **A deterministic verifier checks every claim** against the real repository
   (file exists, line range is real, quoted text appears in the cited evidence,
   commit hash is real) and **keeps**, **flags** ("unverified"), or **drops** each
   claim before it reaches the user.

Only **3–6 evidence snippets** are ever sent to the model — never whole files or
repositories. This keeps the whole thing fast enough to run on a CPU-only machine.

---

## AI component

| Item | Value |
|---|---|
| Model (default) | `qwen2.5-coder:3b` (`config.MODEL_NAME`) |
| Optional larger model | `qwen2.5-coder:7b` (`config.OPTIONAL_MODEL_NAME`) |
| Server | [Ollama](https://ollama.com/) at `http://localhost:11434` (`config.OLLAMA_HOST`) |
| Temperature | `0.0` (`config.TEMPERATURE`) |
| Output format | JSON (`format="json"`), retried once on invalid JSON |

**What the model does.** It receives the user's question, the detected intent, and
up to six evidence snippets. It returns JSON claims, each with a `statement`, an
`evidence_ids` list, a `label` (`stated` / `inferred` / `not_documented`), and — for
`stated` claims — a short exact `quote` copied from the cited evidence. It also
lists what cannot be confirmed from the code (`cannot_confirm`).

**What the model does *not* do.** It never chooses tools, never searches the
repository, never retrieves files, and never checks its own claims. Those are all
deterministic code. The model is only an *explainer* over evidence it is handed.

**No cloud or proprietary AI APIs are used anywhere, and there is no fallback to
them.** The only network calls are (a) the shallow `git clone` of the target public
repository, (b) one bounded `git fetch --deepen` on that same clone for `why`
questions, and (c) `localhost:11434`.

---

## System architecture

### High-level view

```
                         ┌────────────────────────────────────────┐
                         │              Entry points               │
                         │   app.py            repopilot/cli.py    │
                         │  (Streamlit UI)        (command line)   │
                         └───────────────────┬────────────────────┘
                                             │  answer(url, question)
                                             ▼
                         ┌────────────────────────────────────────┐
                         │            repopilot/pipeline.py        │
                         │   orchestrates the run, caches Results   │
                         └───────────────────┬────────────────────┘
                                             │
        ┌────────────────────────────────────┼───────────────────────────────────┐
        ▼                                    ▼                                   ▼
┌───────────────┐                   ┌─────────────────┐                 ┌────────────────┐
│  loader.py    │                   │   router.py     │                 │    llm.py      │
│ validate URL  │                   │ classify intent │                 │ Ollama client  │
│ clone --depth │                   │ gather evidence │                 │ prompt + JSON  │
│ inventory     │                   └────────┬────────┘                 └───────┬────────┘
└───────┬───────┘                            │                                  │
        │ inventory(files)         ┌──────────┼──────────┐                       │
        │                          ▼          ▼          ▼                       │
        │                   ┌──────────┐ ┌──────────┐ ┌──────────┐              │
        │                   │catalog.py│ │history.py│ │indexer.py│              │
        │                   │ scaling  │ │ git why  │ │  AST     │              │
        │                   │ patterns │ │ rationale│ │ callgraph│              │
        │                   └────┬─────┘ └────┬─────┘ └────┬─────┘              │
        │                        └────────────┼────────────┘                    │
        │ evidence (raw)                      ▼                                 │
        │                          ┌─────────────────────┐                       │
        └─────────────────────────▶│    evidence.py      │                       │
                                   │ Evidence + E1,E2,...│                       │
                                   └──────────┬──────────┘                       │
                                              │ EvidenceCollection               │
                                              ▼                                  │
                                   ┌─────────────────────┐   JSON claims         │
                                   │      llm.py         │◀──────────────────────┘
                                   │  explain(...)       │
                                   └──────────┬──────────┘
                                              │ raw claim dicts
                                              ▼
                                   ┌─────────────────────┐
                                   │    verifier.py      │  <-- NO LLM, deterministic
                                   │ resolve ids, lines, │
                                   │ quotes, commit hash │
                                   └──────────┬──────────┘
                                              │ kept / flagged / dropped
                                              ▼
                                   ┌─────────────────────┐
                                   │  pipeline.Result    │
                                   │  + ui.build_answer_ │
                                   │  view(...)          │
                                   └─────────────────────┘
```

### Modules and responsibilities

| Module | Responsibility | Key API |
|---|---|---|
| `repopilot/config.py` | All tunable constants: model, limits, ignore lists, dependency/config filenames, scaling tags, safety constants. No secrets, no absolute paths. | constants |
| `repopilot/loader.py` | Strict GitHub URL validation, shallow `git clone` (argument list, never a shell), file inventory, size/count limits. | `normalize_url`, `clone_repo`, `inventory` |
| `repopilot/evidence.py` | The evidence data model (`Evidence`) and `EvidenceCollection`, which assigns stable `E1, E2, …` ids. | `Evidence`, `EvidenceCollection` |
| `repopilot/catalog.py` | Scaling-pattern catalog (regex detectors per tag) + path classification; produces `file:line` evidence for scaling. | `detect`, `classify_path`, `tags` |
| `repopilot/history.py` | Shallow-aware `git` history for `why`: never reports a boundary commit as the introducer, one bounded deepen, `not_documented` on truncation. | `first_commit_for_path`, `commits_for_path`, `doc_lines_mentioning` |
| `repopilot/indexer.py` | Python-only `ast` index: functions, classes, imports, best-effort call edges. Nothing is imported or executed. | `build_index`, `PythonIndex` |
| `repopilot/router.py` | Keyword intent classification and per-intent evidence gathering, capped and de-duplicated. | `classify`, `gather`, `extract_topic` |
| `repopilot/llm.py` | Ollama wrapper: builds a tight prompt, requests JSON, parses (tolerating code fences), retries once, raises friendly errors. | `explain`, `build_prompt` |
| `repopilot/verifier.py` | **Deterministic** claim checker. Resolves every cited evidence id against the real repo and keeps/flags/drops each claim. | `verify_claims`, `inject_fake_claim` |
| `repopilot/pipeline.py` | Orchestration: clone → route → gather → explain → verify, plus the JSON cache. Always returns a `Result`, never crashes. | `answer`, `answer_path`, `Result` |
| `repopilot/ui.py` | Pure presentation helpers (no Streamlit import) so rendering is unit-testable offline. | `build_answer_view`, `citation_label`, `verifier_badge` |
| `repopilot/cli.py` | Command-line entry point. | `main` |
| `app.py` | Thin Streamlit page that consumes `ui.build_answer_view`. | `main`, `render` |

### End-to-end data flow

1. **Entry.** `app.py` (UI) or `cli.py` calls `pipeline.answer(url, question, …)`.
2. **Validate + cache lookup.** `loader.normalize_url` checks the URL against a
   strict regex. With caching on, `pipeline` hashes `(url, question, inject_fake)`
   to `cache/<sha256[:16]>.json` and returns the cached `Result` if present.
3. **Clone.** `loader.clone_repo` runs `git clone --depth 200` (argument list, no
   shell) into a temp directory. Cloud/git failures become plain-language errors.
4. **Inventory.** `loader.inventory` walks the checkout, skipping ignored
   directories, binary extensions, and files over 200 KB. More than 400 source
   files raises `RepoTooLarge`.
5. **Route.** `router.classify` maps the question to an intent, then
   `router.gather` collects and caps evidence for that intent.
6. **Evidence ids.** The raw items are wrapped in an `EvidenceCollection`, fixing
   the `E1, E2, …` ids the model will cite and the verifier will later resolve.
7. **Explain.** `llm.explain` sends only the question, intent, and up to six
   snippets to `qwen2.5-coder:3b` and gets JSON claims back.
8. **Verify.** `verifier.verify_claims` resolves every cited id against the real
   checkout (file lines, quotes, commit hashes) and produces `kept` / `flagged` /
   `dropped` results plus a badge string.
9. **Assemble.** `pipeline` builds a `Result` with claims, dropped claims, the
   "Not found" categories, the "Cannot confirm from code" list, and an evidence
   snapshot (pre-rendered numbered line blocks) for offline display.
10. **Render.** The CLI prints the result; the UI turns it into an `AnswerView`
    with labeled claims and expandable `path:start-end` citation blocks.
11. **Cache.** On success, the `Result` is written to `cache/` for instant,
    offline replays of the demo.

### Intent routing

The router is deliberately simple and keyword-based; the model never picks tools.

| Intent | Trigger examples | Evidence gathered |
|---|---|---|
| `scaling` | scale, "large number", "number of users", traffic, throughput, concurrent | `catalog.detect` across all scaling tags, interleaved round-robin, capped at 6 |
| `why` | why, reason, chosen, "instead of", rationale | introducing commit + recent commits for the most specific topic file, plus doc lines and one code line mentioning the topic |
| `trace` | "what happens when", walk me through, flow, trace, call graph | Python `ast` call chain from the best-matching symbol, up to depth 4 |
| `overview` | overview, "what does", "what is this", "explain this project" | README head, dependency files, entry points |
| `general` | anything else | keyword-ranked lines across the repo (dependency < config < code < doc) |

### Evidence model

Every piece of evidence (`repopilot/evidence.py`) is a verifiable fact:

- `id` — `E1`, `E2`, … assigned by `EvidenceCollection`
- `kind` — `code` \| `config` \| `dependency` \| `doc` \| `commit`
- `path`, `start_line`, `end_line` — the exact location
- `text` — the exact line(s) or commit message
- `commit` — the hash when `kind == "commit"`
- `tag` — e.g. `caching`, `horizontal_scaling`, `why`, `trace`

`Evidence.location()` renders the human citation: `path:12`, `path:12-14`, or
`commit <hash>`.

### The verifier (the trust boundary)

`repopilot/verifier.py` is fully deterministic and **never calls a language model**.
For each model claim it applies:

1. **Shape.** `statement` present; `label` is one of
   `stated`/`inferred`/`not_documented`; `evidence_ids` is a list of strings.
2. **Ids exist.** Every cited id must exist in the evidence set —
   *a single unknown id drops the whole claim* (reason:
   `fabricated or unknown evidence id(s): …`).
3. **Evidence is real.** File evidence must resolve **inside** the repo (no path
   traversal, no absolute paths) and have a real in-range line span; commit
   evidence must be a real hash (`git cat-file -e`).
4. **Quotes.** A `stated` claim needs a quote that appears in the cited evidence
   after whitespace/case normalization. Matching is **line-by-line**: each
   substantive newline-separated segment (≥ 4 chars) must appear in at least one
   cited item; short fragments are ignored; at least one substantive segment is
   required; segments are never matched against a concatenation of items.
5. **No evidence, no claim.** A claim with no verifiable evidence is dropped;
   `inferred` claims must still cite at least one verified item. `not_documented`
   claims are exempt from the evidence requirement.

Outcomes:

| Status | Meaning | Shown as |
|---|---|---|
| `kept` | All checks passed | declared label (`stated` / `inferred` / `not_documented`) |
| `flagged` | Evidence is real but the quote didn't match | **`unverified`** + a warning |
| `dropped` | Fabricated/unknown id, missing evidence, or malformed | not shown as an answer (listed under "Dropped") |

The badge summarizes the run: `"{kept} verified, {flagged} flagged, {dropped} dropped"`.

### Safety and determinism design

- **Target repositories are untrusted data.** Nothing from a target repo is ever
  imported, compiled, or executed. Files are only read as text and matched.
- **No shell.** `git` is always invoked as an argument list; user input is never
  interpreted by a shell.
- **No escape.** Inventory skips symlinks/file paths that resolve outside the
  root; the verifier rejects absolute paths and `..` traversal.
- **No cloud AI.** The only model endpoint is `localhost:11434`.
- **Deterministic trust boundary.** The verifier contains no randomness and no
  model call; given the same model output, it always returns the same verdict.
- **No crashes.** Missing git, an unreachable Ollama, a bad URL, or a too-large
  repo all return a friendly message in `Result.error`.

### Caching

`pipeline` caches each successful `Result` as JSON under `cache/`, keyed by
`sha256(url + "\x00" + question + "\x00" + inject_fake)[:16]`. This makes the demo
instant and fully offline. The cache stores the full result including an evidence
snapshot (with pre-rendered, numbered line blocks), so the UI can expand citations
without a live checkout. `--no-cache` bypasses it.

---

## Setup

**Prerequisites:** Python **3.10+**, `git`, and [Ollama](https://ollama.com/).
Tested on Windows with Python 3.13; it is plain Python and should work on
Linux/macOS too.

1. **Install and start Ollama, then pull the model:**

   ```bash
   ollama pull qwen2.5-coder:3b      # ~2 GB; qwen2.5-coder:7b is optional
   ollama serve                      # usually already running as a service
   ```

2. **Get the code and install dependencies** (a virtual environment is recommended):

   ```bash
   git clone https://github.com/<owner>/RepoPilot.git
   cd RepoPilot
   python -m venv .venv
   # Windows:  .venv\Scripts\activate
   # Linux/mac: source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Run the offline test suite** (no network, no model needed):

   ```bash
   python -m pytest tests -q
   ```

> The setup instructions were verified from a clean folder: a fresh virtual
> environment built only from `requirements.txt` runs the full suite green.

---

## Usage and demo

### Command line

```bash
# Scaling question (clones + calls the local model; needs network + Ollama)
python -m repopilot.cli https://github.com/docker/awesome-compose "How does this system handle a large number of users?"

# Show the verifier catching a fabricated citation live
python -m repopilot.cli https://github.com/docker/awesome-compose "How does this system handle a large number of users?" --inject-fake

# Why question
python -m repopilot.cli https://github.com/dockersamples/example-voting-app "Why is redis used in this project?"

# Other flags
python -m repopilot.cli <url> "<question>" --no-cache     # ignore the cache
python -m repopilot.cli <url> "<question>" --json         # raw result as JSON
```

### Web UI

```bash
streamlit run app.py
```

Then, in the browser:

1. Paste a GitHub URL.
2. Type a question (the box defaults to the headline scaling question).
3. Optionally tick **"Demo: inject a fabricated citation"**.
4. Click **Ask**.

The page shows the intent, the labeled answer, an expandable citation block for
each `path:start-end` (with the cited lines numbered), a small verifier badge, the
**Not found** categories, and the **Cannot confirm from code** list.

### Demo repositories (cached for offline replay)

| Repository | Question | Result (as cached) |
|---|---|---|
| `docker/awesome-compose` | *How does this system handle a large number of users?* | `1 verified, 5 flagged, 0 dropped` |
| `docker/awesome-compose` | *… + `--inject-fake`* | `0 verified, 3 flagged, 1 dropped` |
| `docker/awesome-compose` | *Why is redis used in this project?* | `0 verified, 1 flagged, 0 dropped` |
| `dockersamples/example-voting-app` | *Why is redis used in this project?* | `1 verified, 0 flagged, 0 dropped` |
| `dockersamples/example-voting-app` | *How does this system handle a large number of users?* | `0 verified, 1 flagged, 0 dropped` |

### Example output: a scaling answer (`docker/awesome-compose`)

```text
Repository: https://github.com/docker/awesome-compose
Question: How does this system handle a large number of users?
Intent: scaling
(cached result)

Answer:
  - [unverified] This system uses Redis for caching and session management.  (flask-redis/requirements.txt:2)
      warning: quote not found in the cited evidence
  - [unverified] The system is configured to handle multiple Kafka brokers.  (wasmedge-kafka-mysql/compose.yml:9)
      warning: quote not found in the cited evidence
  - [inferred] The system is designed to handle a large number of users.  (wasmedge-kafka-mysql/README.md:99)
  - [unverified] The system uses Nginx as a reverse proxy for load balancing.  (nginx-nodejs-redis/nginx/nginx.conf:1)
      warning: quote not found in the cited evidence
  - [unverified] The system uses Bootstrap for frontend styling.  (wasmedge-mysql-nginx/frontend/index.html:99)
      warning: quote not found in the cited evidence
  - [unverified] The system uses FastAPI for building the backend API.  (fastapi/requirements.txt:1)
      warning: quote not found in the cited evidence

Not found: rate_limiting

Cannot confirm from code:
  - The system's ability to handle a large number of users is not explicitly documented.

Verifier: 1 verified, 5 flagged, 0 dropped
```

> Note the honest behavior: the citations are **real** (`flask-redis/requirements.txt:2`
> really does contain `redis`), but the small model often prefixes its quote with
> the citation label (e.g. `flask-redis/requirements.txt:2\nredis`), so the quote
> check fails and the claim is shown as **unverified** rather than trusted. The
> verifier is doing its job.

### Example output: a rationale answer (`dockersamples/example-voting-app`)

```text
Question: Why is redis used in this project?
Intent: why

Answer:
  - [stated] Redis is used in this project to collect new votes.  (README.md:54)
      quote: 'A [Redis](https://hub.docker.com/_/redis/) which collects new votes'

Verifier: 1 verified, 0 flagged, 0 dropped
```

### Example output: the fake-citation demo (`--inject-fake`)

```text
Verifier: 0 verified, 3 flagged, 1 dropped

Dropped (failed verification):
  - Redis clusters shard the cache across 12 nodes for 5 million users.
      reason: fabricated or unknown evidence id(s): E999
```

---

## Testing

The suite is **offline by default**. Network and model tests are opt-in:

```bash
python -m pytest tests -q                                   # offline suite
REPOPILOT_RUN_NETWORK_TESTS=1 python -m pytest tests -q     # + real clone tests
REPOPILOT_RUN_OLLAMA_TESTS=1  python -m pytest tests -q     # + real Ollama tests
```

Current status: **117 passed, 2 skipped** (the 2 skips are the opt-in network and
Ollama guards). Coverage includes the loader/URL rules, catalog detectors, history
(including the shallow-boundary and deepening behavior), the AST index, prompt
building and JSON parsing, the verifier rules (including path traversal and fake
ids), the pipeline/cache, and the UI view builders (via Streamlit's `AppTest`).

---

## Design decisions and constraints

- **One step at a time.** The project was built module by module, in the order of
  the spec, running each module's acceptance test before moving on.
- **The verifier is the product.** The most important guarantee is not the model's
  prose but that no unsupported claim survives unchecked.
- **Small model, big scaffolding.** A 3B CPU model is weak on its own; the
  deterministic router, catalog, history reader, and AST index do the hard work so
  the model only has to summarize.
- **Honesty over confidence.** Flags (`unverified`), `missing` categories, and
  `cannot_confirm` notes are first-class output, not failures.
- **Untrusted input.** Repo content is data, never code; `git` is always an
  argument list; paths are always confined to the checkout.
- **Offline.** After setup, the demo runs with no network and no cloud AI.

---

## Limitations and future work

- **Large repositories:** repos with more than **400 source files** are rejected
  with a friendly message. *Future:* sampling and a large-repo mode.
- **Languages:** call-graph (`trace`) features are **Python-only** via the built-in
  `ast` module. *Future:* other languages via tree-sitter.
- **PRs and issues:** out of scope by design (no GitHub API). *Future:*
  pull-request and issue context for `why` questions.
- **Model strength:** a 3B CPU model is limited and non-deterministic, so wording
  and flag counts vary between runs (the *verifier* is deterministic). *Future:*
  larger local models and stronger prompting (e.g. instructing the model to output
  only the quote text, which would move many flagged claims to verified).
- **Shallow history:** clones use `--depth 200`; the oldest visible commit is never
  reported as the true introducer, and `why` questions allow exactly one bounded
  `git fetch --deepen` (step 200, cap 5000). If history stays truncated the answer
  becomes `not_documented` with reason `history truncated`.
- **Cache:** keyed by `(URL, question, inject-fake)` with no TTL. *Future:* cache
  versioning/invalidation.
- **Long lines:** minified files can produce a very long line in a citation block.
  *Future:* truncate displayed evidence text.
- **Keyword detection:** the scaling catalog is text/regex based and can over- or
  under-detect. *Future:* more precise detectors.

---

## What we built vs. what libraries provide

**We built (original contributions):** the evidence engine and data model, the
scaling-pattern catalog and detectors, the git-history rationale finder, the
deterministic verifier, the question router and per-intent evidence gathering, the
Python AST index and `trace` call path, the pipeline/CLI/UI, and all tests and
fixture repositories.

**Provided by third-party libraries:**

| Component | Role | Provider |
|---|---|---|
| Ollama | Local model server at `localhost:11434` | Ollama |
| `ollama` (Python) | Client library for the model server | Ollama |
| Qwen2.5-Coder (3B / 7B) | The open-weight model that explains evidence | Alibaba Qwen |
| Streamlit | Prototype web UI | Streamlit |
| pytest | Test runner (development only) | pytest |

**AI-assisted development tool used:** this project was implemented with an AI
coding agent working module by module from the specification in
`docs/REPOPILOT_SPEC.md`. The team reviewed and directed every step.

### Repository layout

```
RepoPilot/
├── app.py                    # Streamlit UI entry point (streamlit run app.py)
├── repopilot/
│   ├── __init__.py
│   ├── config.py             # constants: model, limits, ignore lists, tags
│   ├── loader.py             # URL validation + clone + inventory
│   ├── evidence.py           # Evidence model + id-assigning collection
│   ├── catalog.py            # scaling-pattern catalog + detectors
│   ├── history.py            # shallow-aware git history for "why"
│   ├── indexer.py            # Python ast index (symbols, calls, imports)
│   ├── router.py             # intent classification + evidence gathering
│   ├── llm.py                # Ollama wrapper, prompt, JSON parsing
│   ├── verifier.py           # deterministic claim checker (no LLM)
│   ├── pipeline.py           # orchestration + result cache
│   ├── ui.py                 # pure presentation helpers
│   └── cli.py                # command-line entry point
├── cache/                    # cached results for the demo repositories
├── docs/REPOPILOT_SPEC.md    # the build specification
├── tests/                    # offline tests + tiny fixture repos
├── requirements.txt
├── LICENSE
└── README.md
```

---

## Project status

Built end to end: loader, evidence model, scaling catalog, deterministic verifier,
local Ollama explanation, shallow-history rationale, question router, pipeline +
cache, Python AST `trace`, CLI, and Streamlit UI. Full offline suite:
**117 passed, 2 skipped**.

---

## Team

- **Abdur Rafay**
- **Zain ul Abidin**

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Abdur Rafay and Zain ul Abidin.

## Acknowledgements

- The hackathon brief that defined this prototype's constraints and scope.
- [Ollama](https://ollama.com/) for local, offline model serving.
- The Qwen team for the open-weight **Qwen2.5-Coder** models.
- [Streamlit](https://streamlit.io/) for the UI framework.
- [pytest](https://pytest.org/) for the test runner.

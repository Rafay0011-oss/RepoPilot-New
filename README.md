# RepoPilot

**Plain code finds and checks the evidence. A small local open-weight model reads it and explains it in simple words.**

RepoPilot is an **offline, evidence-first** prototype for asking questions about a
public GitHub repository. You paste a repository URL, ask a question, and every
claim in the answer points to a real `file:line` or a real commit. A
**deterministic verifier** — no AI — checks each claim before it is shown.

The headline questions it is built to handle:

> "How does this system handle a large number of users?"
> "Why is this method (for example, authentication) used here?"

A repository can show **what mechanisms exist**, but it cannot **prove capacity**,
and it cannot always explain **why** a choice was made. RepoPilot says so
honestly instead of guessing.

---

## Problem

Reading an unfamiliar codebase to answer "does this scale?" or "why is this here?"
is slow and error-prone. Large language models will happily answer those questions
from general knowledge, but they **hallucinate** project-specific facts and cannot
be trusted to cite real code. On a CPU-only laptop, and with a strict
no-cloud-AI rule, you also cannot send whole repositories to a model.

## Solution

RepoPilot separates *finding/checking facts* from *explaining them*:

1. **Deterministic code gathers evidence.** A catalog of scaling patterns, a
   shallow `git` history reader, a Python AST index, and a keyword router collect a
   handful of exact `file:line` snippets (or commit messages).
2. **A small local model explains that evidence** in plain words and returns
   structured JSON claims, each labelled and citing evidence ids.
3. **A deterministic verifier checks every claim** against the real repository
   (file exists, line range is real, quoted text appears in the cited evidence,
   commit hash is real) and keeps, flags ("unverified"), or drops each claim.

Only **3–6 evidence snippets** are ever sent to the model — never whole files or
repositories. This keeps it fast enough for a CPU-only machine.

## AI Component

- **Model:** `qwen2.5-coder:3b` (default), optional `qwen2.5-coder:7b`
  (`OPTIONAL_MODEL_NAME` in `repopilot/config.py`).
- **Served by:** [Ollama](https://ollama.com/) at `http://localhost:11434`.
- **Sampling temperature:** `0.0` (`config.TEMPERATURE`).
- **What the model does:** it receives the user's question, the detected intent,
  and up to six evidence snippets, and returns JSON claims. Each claim has a
  `statement`, an `evidence_ids` list, a `label` (`stated` / `inferred` /
  `not_documented`), and — for `stated` claims — a short exact `quote` copied from
  the cited evidence. It also lists what cannot be confirmed from the code.
- **What the model does *not* do:** it never chooses tools, never searches the
  repository, and never checks its own claims. Those are all deterministic code.
- **No cloud or proprietary AI APIs are used anywhere, and there is no fallback to
  them.** The only network calls are the `git clone` of the target public repo, one
  bounded `git fetch --deepen` on that same clone for `why` questions, and
  `localhost:11434`.

## Architecture

```
RepoPilot/
├── app.py                   # Streamlit UI (thin page; built last)
├── repopilot/
│   ├── config.py            # constants: model, limits, ignore lists, tags
│   ├── loader.py            # strict GitHub URL validation + clone + inventory
│   ├── indexer.py           # Python ast index: functions, classes, imports, calls
│   ├── catalog.py           # scaling-pattern catalog + detectors
│   ├── history.py           # git log/deepen helpers for rationale ("why") evidence
│   ├── evidence.py          # Evidence dataclass + id-assigning collection
│   ├── llm.py               # Ollama wrapper, prompt, JSON parsing + retry
│   ├── verifier.py          # deterministic claim checker (NO LLM, ever)
│   ├── router.py            # question -> intent -> which evidence to gather
│   ├── pipeline.py          # ties everything together + result cache
│   ├── ui.py                # pure presentation helpers (no Streamlit import)
│   └── cli.py               # command-line entry point
├── cache/                   # cached results for the demo repositories
├── docs/REPOPILOT_SPEC.md   # the build specification
└── tests/                   # offline tests + tiny fixture repos
```

**Flow:** `pipeline.answer(url, question)` → `loader` clones → `router` picks an
intent and gathers capped evidence (using `catalog`, `history`, `indexer`) →
`llm.explain` produces JSON claims → `verifier.verify_claims` resolves every id
against the repository → a `Result` with cleaned claims, labels, citations, the
"cannot confirm" list, missing categories, and a verifier badge. Results are
cached per `(URL, question, inject-fake)` under `cache/` to make demos instant.

**Design rules baked in:** the target repository's code is treated as untrusted
data and is never executed; `git` is always invoked with an argument list (never a
shell string); the verifier is fully deterministic and LLM-free.

## Setup

Requires **Python 3.10+**, **git**, and **Ollama**. Tested on Windows with Python
3.13; it is plain Python and should work on Linux/macOS too.

1. **Install and start Ollama**, then pull the model:
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

## Demo

### Command line

```bash
# Ask about a real public repo (clones + calls the local model; needs network + Ollama)
python -m repopilot.cli https://github.com/docker/awesome-compose "How does this system handle a large number of users?"

# Show the verifier catching a fabricated citation live
python -m repopilot.cli https://github.com/docker/awesome-compose "How does this system handle a large number of users?" --inject-fake

# Ignore the cache / print raw JSON
python -m repopilot.cli https://github.com/docker/awesome-compose "Why is redis used in this project?" --no-cache
python -m repopilot.cli https://github.com/docker/awesome-compose "How does this system handle a large number of users?" --json
```

The scaling question on a cached demo repository prints an intent, a labeled
claim list with `file:line` citations, a **Not found** line for categories with no
evidence, a **Cannot confirm from code** section, and a verifier badge such as
`1 verified, 5 flagged, 0 dropped`. The `--inject-fake` run adds a dropped claim
with the reason `fabricated or unknown evidence id(s): E999`.

### Web UI

```bash
streamlit run app.py
```

Paste a URL, ask a question, and the page shows the intent, the labeled answer
with expandable citations (each labelled `path:start-end` and showing the cited
lines), the verifier badge, **Not found** categories, and **Cannot confirm from
code**. A **"Demo: inject a fabricated citation"** checkbox runs the fake-citation
demo from the UI. Cached demo repositories answer instantly and offline.

### Demo repositories (2)

Both are cached under `cache/` for instant, offline demos, and both were tested
with a scaling question and a `why` question:

1. [`docker/awesome-compose`](https://github.com/docker/awesome-compose) — Redis,
   Kafka and Nginx examples.
2. [`dockersamples/example-voting-app`](https://github.com/dockersamples/example-voting-app)
   — Redis + worker + Kubernetes replica manifests.

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

---

## What we built vs. what libraries provide

**We built (original contributions):** the evidence engine and data model, the
scaling-pattern catalog and detectors, the git-history rationale finder, the
deterministic verifier, the question router and intent-specific evidence
gathering, the Python AST index and `trace` call-path, the pipeline/CLI/UI, and
all tests and fixture repositories.

**Provided by third-party libraries:**

| Component | Role | Provider |
|---|---|---|
| Ollama | Local model server at `localhost:11434` | Ollama |
| `ollama` (Python) | Client library for the model server | Ollama |
| Qwen2.5-Coder (3B / 7B) | The open-weight model that explains evidence | Alibaba Qwen |
| Streamlit | Prototype web UI | Streamlit |
| pytest | Test runner (development only) | pytest |

**AI-assisted development tool used:** this project was implemented with the
**OpenCode** AI coding agent, working module by module from the specification in
`docs/REPOPILOT_SPEC.md`. The team reviewed and directed every step.

## Prototype limits and future work

- **Large repositories:** repos with more than **400 source files** are rejected
  with a friendly message. *Future:* sampling and a large-repo mode.
- **Languages:** call-graph (`trace`) features are **Python-only** via the built-in
  `ast` module. *Future:* other languages via tree-sitter.
- **PRs and issues:** out of scope by design (no GitHub API). *Future:* pull-request
  and issue context for "why" questions.
- **Model strength:** a 3B CPU model is limited and non-deterministic, so wording
  and flag counts vary between runs (the *verifier* is deterministic). *Future:*
  larger local models and stronger prompting.
- **Shallow history:** clones use `--depth 200`; the oldest visible commit is never
  reported as the true introducer, and `why` questions allow exactly one bounded
  `git fetch --deepen`. If history stays truncated the answer becomes
  `not_documented` (reason: `history truncated`).
- **Cache:** keyed by `(URL, question, inject-fake)` with no TTL. *Future:* cache
  versioning/invalidation.
- **Very long lines:** minified files can produce a very long evidence line in a
  citation block. *Future:* truncate displayed evidence text.
- **Keyword detection:** the scaling catalog is text/keyword based and can over- or
  under-detect. *Future:* more precise detectors.

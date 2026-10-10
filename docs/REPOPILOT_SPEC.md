# RepoPilot: Build Specification

> Give this whole file to the coding agent. Tell it: "Read the spec fully. Build ONE module at a time in the order of Section 11. After each module, run its acceptance test, report the result, and wait for me before continuing."

---

## 1. What we are building

**RepoPilot** is an offline prototype. A user pastes a public GitHub repository URL, then asks questions about the code. RepoPilot answers with **evidence**: every claim points to a real `file:line` or a real commit, and a deterministic verifier checks each claim before it is shown.

**Headline question it must handle well:**
> "How does this system handle a large number of users?" and "Why is this method (for example, authentication) used here?"

A repo can show *what mechanisms exist* but cannot *prove capacity* or always explain *why*. RepoPilot must say so honestly (see Section 7).

**One-line pitch:** *Plain code finds and checks the evidence. A small local open-weight model reads it and explains it in simple words.*

---

## 2. Hard constraints (from the hackathon brief, mandatory)

1. **Open-weight AI must be central.** The explaining and judging is done by a **Qwen coder model served locally by Ollama** (default `qwen2.5-coder:3b`, optional `qwen2.5-coder:7b`).
2. **No cloud or proprietary AI APIs anywhere in the project.** No fallback to them either.
3. **Original work.** The original contributions are the evidence engine, the scaling-pattern catalog, the git-history rationale finder, and the verifier. Third-party libraries must be listed in the README.
4. **Public GitHub repo with an open-source license** (MIT, file named `LICENSE`).
5. **No secrets** (keys, tokens, passwords) in the repo. The project needs none.
6. **README** follows this order: Project Name, Problem, Solution, AI Component, Architecture, Setup, Demo, Team, License, Acknowledgements.
7. If packaged as an Agent Skill, it must follow the Agent Skills standard exactly. **Do not create a skill folder until we confirm the standard's required structure.** Skip it for now.

---

## 3. Environment limits (design for these)

- Laptop: **16 GB RAM, CPU only, no GPU.**
- Fully offline after setup. The only network calls allowed: `git clone` of the target repo, one bounded `git fetch --deepen` on that same already-cloned repo **for `why` questions only**, and `localhost:11434` (Ollama).
- Small model, slow generation. Therefore: **send the model only 3 to 6 evidence snippets per call**, never whole files or whole repos.
- Python 3.10+. Prefer the standard library. Keep dependencies few.

---

## 4. Scope of the prototype

**In scope**
- Public GitHub URL input, shallow clone.
- Repos of **at most 400 source files**. Larger repos get a clear, friendly "too large for this prototype" message.
- Language-agnostic detection of scaling mechanisms (reads dependency files, config files, and does text search).
- Python-only call-graph features (using the built-in `ast` module).
- Q&A with verified citations and the labels `stated`, `inferred`, `not_documented`.

**Out of scope (do NOT build)**
- Cloud models, authentication to GitHub, GitHub API (PRs and issues), databases, user accounts.
- Languages other than Python for call-graph features.
- Fine-tuning or training any model.
- An open-ended autonomous agent loop (see Section 8).
- Any feature not listed in this spec.

---

## 5. Folder layout

```
repopilot/
├── LICENSE                  # MIT
├── README.md
├── requirements.txt
├── repopilot/
│   ├── __init__.py
│   ├── config.py            # constants: model name, limits, ignore lists
│   ├── loader.py            # clone + file inventory
│   ├── indexer.py           # Python ast index: functions, classes, imports, calls
│   ├── catalog.py           # scaling-pattern catalog + detectors
│   ├── history.py           # git log / blame helpers for rationale evidence
│   ├── evidence.py          # Evidence dataclass + collection
│   ├── llm.py               # Ollama wrapper, prompt, JSON parsing
│   ├── verifier.py          # deterministic claim checker (NO LLM)
│   ├── router.py            # question -> intent -> which evidence to gather
│   ├── pipeline.py          # ties everything together
│   └── cli.py               # command-line entry
├── app.py                   # simple Streamlit UI (built last)
├── cache/                   # cached results for demo repos
└── tests/
    ├── test_verifier.py
    ├── test_catalog.py
    └── fixtures/            # tiny fake repos used by tests
```

---

## 6. Core data model

```python
@dataclass
class Evidence:
    id: str            # "E1", "E2", ...
    kind: str          # "code" | "config" | "dependency" | "commit" | "doc"
    path: str | None   # repo-relative file path
    start_line: int | None
    end_line: int | None
    text: str          # the exact text (code lines or commit message)
    commit: str | None # commit hash for kind == "commit"
    tag: str | None    # e.g. "caching", "queue" (from the catalog)
```

The model may only cite evidence by `id`. The verifier then resolves each id back to real data.

**Model output contract (JSON only):**

```json
{
  "claims": [
    {
      "statement": "Redis is used to cache product pages.",
      "evidence_ids": ["E3", "E5"],
      "label": "stated | inferred | not_documented",
      "quote": "exact short text copied from the cited evidence (required if label is stated)"
    }
  ],
  "cannot_confirm": ["short plain-language limits, e.g. actual traffic capacity"]
}
```

Use Ollama's JSON output mode (`format` parameter). Check the current Ollama Python docs for the exact usage. On invalid JSON, retry once, then fail gracefully with a readable error.

---

## 7. Honesty rules (product behavior)

Every statement shown to the user carries one label:

- **stated**: a source in the repo says it directly, with a verified quote.
- **inferred**: a reasonable conclusion from verified code, clearly marked as a guess.
- **not_documented**: the repo does not explain this.

Always end scaling answers with a **"Cannot confirm from code"** section. Never say "this system scales to N users". Say what mechanisms exist and what could not be seen (deployment settings, traffic, hardware).

If no evidence is found for a category, report it as **Not found**. Never invent a reason.

---

## 8. Router (code decides, the model does NOT choose tools)

Small CPU models choose tools badly. Use a simple keyword router in `router.py`:

| Intent | Trigger words (examples) | Evidence gathered |
|---|---|---|
| `overview` | "what does", "overview", "explain this project" | README, top-level tree, entry-point files, dependency files |
| `scaling` | "scale", "millions", "many users", "traffic", "performance", "handle load" | catalog detectors over config, dependency, code files |
| `why` | "why", "reason", "chosen", "instead of" | topic files (e.g. auth) + `history.py` commits and nearby comments and docs |
| `trace` (stretch) | "what happens when", "flow", "walk me through" | call-graph path via `indexer.py` |
| `general` | everything else | keyword search over symbols and files |

The router returns a list of `Evidence` items, capped at **6** for the model.

---

## 9. Module specifications

### 9.1 `loader.py`
- `clone_repo(url) -> Path`: `git clone --depth 200 <url> <workdir>`. Accept only `https://github.com/<owner>/<repo>` URLs (validate strictly, reject anything else). Never pass user input into a shell string; use `subprocess.run([...])` with a list.
- `inventory(path) -> list[FileInfo]`: ignore `.git`, `node_modules`, `vendor`, `dist`, `build`, `__pycache__`, binary files, and files over 200 KB. If more than 400 source files, raise `RepoTooLarge`.

### 9.2 `indexer.py` (Python only)
- Parse each `.py` file with `ast`. Record: functions, classes, imports, and which functions call which names (best effort).
- Skip files that fail to parse. Never crash on bad syntax.
- Expose `find_symbol(name)`, `callers_of(name)`, `import_graph()`.

### 9.3 `catalog.py` (original contribution)
A table of scaling mechanisms. Each entry has a `tag`, a plain description, and detectors that look at **dependency files** (`requirements.txt`, `pyproject.toml`, `package.json`, `Gemfile`, `go.mod`, `pom.xml`), **config files** (`Dockerfile`, `docker-compose*.yml`, `*.yaml`, `nginx.conf`, Kubernetes manifests), and **source text**.

| tag | example detectors |
|---|---|
| `caching` | redis, memcached, `lru_cache`, `cache_page`, `@cache` |
| `queue_background` | celery, rq, kafka, rabbitmq, sidekiq, bull |
| `db_scaling` | connection pool settings, `pool_size`, `read_replica`, `shard`, `partition`, `CREATE INDEX` |
| `horizontal_scaling` | `replicas:` in k8s/compose, load balancer / upstream blocks, autoscaling configs |
| `stateless_auth` | `jwt`, token-based auth libraries |
| `rate_limiting` | `ratelimit`, `limiter`, `throttle` |
| `async_io` | `asyncio`, `async def`, `aiohttp`, uvicorn/gunicorn worker settings |

Return `Evidence` items with the exact lines. Prefer hits in dependency/config files and real source files over tests and docs (down-rank paths containing `test`, `example`, `docs`).

### 9.4 `history.py` (original contribution)
- `commits_for_path(path, limit=20)`: via `git log` for that path.
- `first_commit_for_path(path)`: the commit that introduced the file.
- `commit_message(hash)`.
- For `why` questions: gather the introducing commit message, relevant nearby comments/docstrings, and any README/CHANGELOG/ADR lines mentioning the topic.
- Many repos have poor messages. That is expected: the answer becomes `not_documented`.

### 9.5 `llm.py`
- One function: `explain(question, evidence, intent) -> dict` that builds the prompt, calls Ollama, and parses JSON.
- Prompt rules (put these in the system prompt):
  1. Use ONLY the provided evidence. Do not use outside knowledge about the project.
  2. Cite evidence by id.
  3. Label every claim `stated`, `inferred`, or `not_documented`.
  4. A `stated` claim must include a short exact quote copied from the cited evidence.
  5. Write for a beginner. Short plain sentences.
  6. List what cannot be confirmed from the code.
  7. Output JSON only, matching the contract in Section 6.
- Default model from `config.py`. Temperature low (0 to 0.2).

### 9.6 `verifier.py` (original contribution, NO LLM, fully deterministic)
Given the model's JSON and the evidence list, check every claim:

1. Every `evidence_id` exists in the evidence list. Else **drop the claim**.
2. For code/config evidence: the file exists inside the cloned repo (reject path traversal), and the line range is within the file length.
3. For `stated` claims: the `quote`, after whitespace/case normalization, must appear in the cited evidence text. If not, **downgrade to flagged "unverified"** (shown with a warning) or drop. Default: flag.
4. For commit evidence: the hash exists (`git cat-file -e <hash>`), and any quoted message text appears in the real commit message.
5. A claim with **no valid evidence** is dropped.
6. `inferred` claims must still cite at least one verified evidence item.
7. Return a report: `kept`, `flagged`, `dropped` counts plus the cleaned claim list.

**Required demo hook:** a `--inject-fake` option (or a UI toggle) that adds a fabricated citation to the model output so we can show the verifier catching it live.

### 9.7 `pipeline.py` and `cli.py`
- `answer(repo_url, question) -> Result`: load, route, gather evidence, call the model, verify, return cleaned claims, labels, citations, the "cannot confirm" list, and the verifier report.
- CLI: `python -m repopilot.cli <repo_url> "<question>"`.
- Cache results per (repo URL, question) under `cache/` to make demos fast.

### 9.8 `app.py` (build LAST)
Streamlit app: URL box, question box, intent shown, answer with a labeled list. Each citation is shown as `path:start-end` with the cited lines in an expandable block. Show a small verifier badge ("5 verified, 1 flagged, 0 dropped"). Stream or show a spinner so slow generation still feels responsive.

---

## 10. Quality bar

- Never crash on bad input: invalid URL, private repo, huge repo, Ollama not running, model returns bad JSON. Each gets a plain-language message.
- Every module has at least one test using small fixture repos in `tests/fixtures/`.
- No network access in tests (mock Ollama).
- No secrets, no hardcoded absolute paths.
- Type hints and short docstrings. Keep functions small.

---

## 11. Build order and acceptance tests

Build in this order. **Stop after each step, run the test, and report.**

1. **Skeleton + config + LICENSE (MIT) + requirements.** Test: package imports.
2. **`loader.py`.** Test: clones a small public repo, returns the inventory, rejects a non-GitHub URL, raises `RepoTooLarge` on a big fixture.
3. **`catalog.py` + `evidence.py`.** Test: on a fixture repo containing `redis` in `requirements.txt` and `replicas: 3` in a compose file, returns correct `file:line` evidence tagged `caching` and `horizontal_scaling`.
4. **`verifier.py`.** Test (most important): a correct claim is kept, a fabricated path is dropped, a wrong quote is flagged, a fake commit hash is dropped.
5. **`llm.py`.** Test: with Ollama running, returns valid JSON for a sample evidence set. With Ollama stopped, returns a friendly error. Retry-once logic works.
6. **`history.py`.** Test: on a fixture git repo with known commits, returns the introducing commit and message.
7. **`router.py` + `pipeline.py` + `cli.py`.** Test: the scaling question on the fixture repo returns verified claims, Not found for missing categories, and a "Cannot confirm" section.
8. **`indexer.py` + `trace` intent** (stretch, only if steps 1 to 7 are solid).
9. **`app.py` UI.** Test: full flow in the browser on a cached demo repo.
10. **README, final checks (Section 12), demo rehearsal.**

---

## 12. Final checklist before submission

- [ ] Public GitHub repo, `LICENSE` file present (MIT).
- [ ] README follows the brief's section order and states the model (name and tag) and what it does.
- [ ] README lists third-party components and the AI-assisted development tools used.
- [ ] README states clearly what the team built versus what libraries provide.
- [ ] README lists prototype limits and future work (large repos, more languages, PRs/issues, stronger models).
- [ ] Setup instructions tested from a clean folder.
- [ ] No secrets in the repo or git history.
- [ ] 2 to 3 demo repos tested and cached; each has real scaling evidence and a findable "why".
- [ ] Fake-citation demo works live.
- [ ] Both team members can explain and modify every module.

---

## 13. Rules for the coding agent

1. Follow this spec. If something is unclear, ask instead of guessing.
2. Build one module at a time and run the acceptance test before moving on.
3. Do not add features, files, or dependencies that are not in this spec.
4. Never call a cloud AI service. The only model is the local Ollama Qwen model.
5. Keep the verifier free of any LLM call. It must be deterministic.
6. Never run shell commands built from raw user input.
7. After each step, print a short summary: what was built, test result, and any assumptions made.

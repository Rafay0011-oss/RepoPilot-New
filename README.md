Objective

Build RepoPilot, an offline evidence-first code Q&A prototype, by implementing the spec's Section 11 steps one at a time, running each acceptance test, reporting real results, and stopping for user approval after each step.
Requirements
Treat docs/REPOPILOT_SPEC.md as source of truth; implement exactly one Section 11 step at a time in order; state intent before each step.
Run acceptance tests and report actual results; never claim a test passed unless run; fix failures before proceeding.
After each step: show files changed, tests run, results, limitations; then STOP and wait for approval.
Never use cloud AI APIs; use local Ollama qwen2.5-coder:3b; keep verifier deterministic and LLM-free.
Treat cloned repo content as untrusted data; never execute code from target repositories; build git commands as argument lists, never shell strings.
Do not silently change scope, architecture, model choice, or dependencies; no unnecessary files.
License copyright line: Copyright (c) 2026 Abdur Rafay and Zain ul Abidin.
Shallow history: oldest visible commit never reported as true introducer; one bounded git fetch --deepen only for why; never crash on fetch failure; return not_documented reason exactly "history truncated".
Verifier quote check (option A): each newline-separated segment must appear in at least one cited item; short fragments ignored; at least one substantive segment required; never match against a concatenation.
Streamlit UI per §9.8: URL box, question box, intent shown, labeled claim list, each citation path:start-end with cited lines in an expandable block, small verifier badge, spinner; UI toggle for the --inject-fake demo hook.
Decisions
Project root = C:\Users\Abdur Rafay\OneDrive\Desktop\RepoPilot; spec copied into docs/.
Dependencies: ollama, streamlit, pytest (all now installed in .venv; streamlit 1.65.0).
Toolchain: default MSYS2 python has no pip; local .venv (Windows Python 3.13) used. Run tests via .\.venv\Scripts\python.exe -m pytest.
Tests offline by default; real clone gated by REPOPILOT_RUN_NETWORK_TESTS=1; real Ollama gated by REPOPILOT_RUN_OLLAMA_TESTS=1.
Verifier strict reading: any unknown cited evidence id drops the whole claim; not_documented claims exempt from evidence; "source file" = config.SOURCE_EXTENSIONS.
Step 8 (indexer + trace) was included (user approved "Continue").
Step 9 decomposition: added repopilot\ui.py (pure, Streamlit-free helpers) + thin app.py at repo root, so rendering is unit-testable offline (§10); disclosed as a deviation from the spec's file list.
Result gained an evidence snapshot field (with pre-rendered numbered line blocks) so the UI can expand citations from a cached result without a live checkout; older cache files still load (field defaults to []).
Cache keeps only the two docker/awesome-compose entries (1679dea7bb8a7872.json = inject_fake, 5fc31a63574dd05c.json = non-fake) as the cached demo; weak/oversized entries removed.
Work State
Completed
Steps 1–8 (skeleton/config/LICENSE/requirements; loader.py; evidence.py+catalog.py; verifier.py; llm.py; history.py; router.py+pipeline.py+cli.py; indexer.py+trace).
Step 9: repopilot\ui.py + app.py (Streamlit UI) + tests\test_ui.py; Evidence.to_dict(); pipeline evidence snapshots/blocks.
Full suite last run: 117 passed, 2 skipped (2 = network/Ollama opt-in guards).
Step 9 acceptance (browser, cached docker/awesome-compose scaling question): intent scaling; badge 1 verified, 5 flagged, 0 dropped; labeled list; expandable citations showed numbered lines (2 | redis); Not found: rate_limiting; Cannot confirm from code. Inject-fake toggle → 0 verified, 3 flagged, 1 dropped with dropped reason fabricated or unknown evidence id(s): E999. Screenshots previewed to user.
Real end-to-end trace (Step 8, temp repo): intent trace, 2 verified, 0 flagged, 0 dropped.
Active
(none) — awaiting user approval to begin Step 10.
Blocked
(none)
Next Move
On approval, implement Step 10: README.md + final checks (spec §12) + demo rehearsal.
README must follow brief's section order, state model (qwen2.5-coder:3b), list third-party components and AI-assisted dev tools, state team-built vs library-provided, list prototype limits/future work, tested setup instructions.
Optionally address limitation: cap displayed evidence block length (very long minified lines).
Relevant Files
docs\REPOPILOT_SPEC.md: source of truth (§9.8 UI, §11 build order, §12 final checklist).
app.py: Streamlit UI entry (streamlit run app.py), root-level.
repopilot\ui.py: pure view builders (build_answer_view, citation_label, citation_block, verifier_badge).
repopilot\pipeline.py: answer, answer_path, Result (incl. evidence snapshots), cache under cache/, _render_block.
repopilot\router.py: classify, gather, trace call-chain (_gather_trace, _follow_calls); scaling triggers include "large number", "number of users".
repopilot\indexer.py: build_index, PythonIndex (find_symbol, callers_of, callees_of, calls_from, import_graph).
repopilot\verifier.py: verify_claims, ClaimResult, inject_fake_claim; citation format path:start-end / commit <hash>.
repopilot\evidence.py: Evidence (location, to_dict), EvidenceCollection.
repopilot\config.py: constants (MODEL_NAME, MAX_EVIDENCE=6, MAX_EVIDENCE_PER_TAG=5, CACHE_DIR="cache", SCALING_TAGS, CLONE_DEPTH=200).
repopilot\cli.py: CLI (--inject-fake, --no-cache, --json).
tests\test_ui.py, tests\test_pipeline.py, tests\test_indexer.py: Step 7–9 coverage (incl. Streamlit AppTest).
tests\fixtures\sample_repo\: fixture (requirements.txt redis/celery/gunicorn, docker-compose replicas, service/cache.py).
cache\5fc31a63574dd05c.json, cache\1679dea7bb8a7872.json: cached docker/awesome-compose demo results (non-fake + fake).
Important Context
Not a git repo; no .gitignore; nothing committed/pushed. .venv/ and cache/ are local artifacts.
Run tests: .\.venv\Scripts\python.exe -m pytest tests -q from project root (python -m pytest puts cwd on sys.path, so import repopilot and import app work).
Local model is non-deterministic (temp ~0.2): same question can yield different counts across runs; verifier is deterministic given model output.
Streamlit UI needs streamlit in .venv (installed); browser acceptance used headless streamlit run app.py --server.headless true --server.port 8501 on http://localhost:8501; agent cannot view images (screenshot read errors), but can browser.preview server-local PNG paths.
Browser tool params: browser.wait needs condition + timeoutMs (max 30000); browser.evaluate param is script; Streamlit inputs set via React native value setter + input/change events; Ask button found by innerText === 'Ask'.
docker/awesome-compose is the cached demo repo (rich scaling evidence: redis/kafka/nginx/fastapi); non-cached repos require network + Ollama.
Acceptance demo question: How does this system handle a large number of users? (matches app default and cache keys).
REPOPILOT_FRONTEND_SPEC.md does not exist on the system.

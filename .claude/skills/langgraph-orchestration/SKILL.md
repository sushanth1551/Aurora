---
name: langgraph-orchestration
description: "LOCAL REFERENCE SKILL (authored for AURORA from official LangGraph docs; not an official LangGraph artifact). Use when implementing or reviewing LangGraph workflows in backend/app/workflows/, agent orchestration, state graphs, conditional routing, checkpointing/persistence, human-in-the-loop approval gates, retries or resumable execution. Covers TypedDict state and reducers, StateGraph nodes/edges, Command and Send routing, checkpointers and thread_id, interrupt()/Command(resume=...), RetryPolicy and error recovery, with AURORA's own rules. Verify APIs against the installed langgraph version before use."
---

# LangGraph orchestration (local reference)

**Status and provenance.** The LangGraph repository ships no Agent Skills, so this file was
written locally for AURORA on 2026-10-31 from the official documentation listed under
*Sources*. Code snippets are reproduced from those pages. It is **not** an official
LangGraph skill and must not be presented as one. LangGraph itself is MIT licensed.

**Version caveat.** `langgraph` 0.6.11 is already installed in AURORA's virtual environment
(declared in `requirements.txt`); nothing here requires installing anything. The documentation
this reference follows describes LangGraph 1.x. Some items below are marked *1.x docs* because
they may not exist in 0.6.11 (for example error-handler nodes, `Overwrite`, `GraphDrained`,
`stream_events(version="v3")`). Check the installed package (`python -c "import langgraph.types as t; print(dir(t))"`)
or the API reference before relying on them.

## AURORA rules that apply to every workflow

From `CLAUDE.md` and `backend/app/workflows/research_workflow.py`:

- Define a `TypedDict` state per workflow (`ResearchState` in `backend/app/research/state.py`
  is the start); keep nodes small and pure where possible. Deterministic work stays in plain
  services (`app.research`, `app.intelligence`); agents are thin coordinators.
- Import `langgraph` **lazily inside the builder function** so modules import without side
  effects and the app starts without any credentials.
- Add bounded retries for *transient* provider errors only (`ProviderRateLimitError`,
  `ProviderUnavailableError`, `ProviderTimeoutError`); never blindly retry non-idempotent writes.
- Insert an `interrupt()` for human approval before any external write (publishing, CMS,
  Search Console). Agents never publish without recorded approval.
- Use a persistent checkpointer (PostgreSQL) before running long or costly workflows, so runs
  can resume; expose run status through the API.
- Collect errors in state; never swallow them or fabricate a successful result.

## 1. State and reducers

```python
from operator import add
from typing import Annotated, Literal
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class State(TypedDict):
    messages: Annotated[list, add_messages]   # merged by message id
    tags: Annotated[list[str], add]           # appended
    count: int                                # no reducer: last write wins
```

- A key without a reducer is overwritten by each update; `Annotated[T, reducer]` attaches a
  reducer that receives `(accumulated, update)`.
- Nodes return **only the keys they change**; reducers decide how each key merges.
- *1.x docs:* returning `Overwrite([])` (from `langgraph.types`) clears a merging-reducer
  field; an empty list alone is merged in and does not clear it.
- For AURORA, list fields such as `sources`, `findings`, `errors` should carry `operator.add`
  so parallel or repeated nodes accumulate instead of overwrite.

## 2. Graph, edges and routing

```python
builder = StateGraph(State)
builder.add_node("discover", discover)          # name defaults to the function name
builder.add_edge(START, "discover")
builder.add_edge("synthesize", END)


def route(state: State) -> Literal["crawl", "synthesize"]:
    return "crawl" if state["count"] < 3 else "synthesize"


builder.add_conditional_edges("discover", route, {"crawl": "crawl", "synthesize": "synthesize"})
graph = builder.compile()                        # structural checks happen here
```

- The path map on `add_conditional_edges` is optional; it translates router return values into
  node names.
- `Command` updates state and routes in one return; the return annotation lets the graph render
  destinations:

```python
from langgraph.types import Command, Send


def node_a(state: State) -> Command[Literal["node_b"]]:
    return Command(update={"count": state["count"] + 1}, goto="node_b")


def fan_out(state: State):                      # map-reduce: one Send per item
    return [Send("worker", {"item": x}) for x in state["items"]]


builder.add_conditional_edges(START, fan_out)
```

- For each node use **either** static edges **or** dynamic routing (`Command` / conditional
  edges), not both: a `Command` `goto` adds an edge and does not replace static ones.
- `recursion_limit` caps super-steps per execution and is passed as a top-level config key
  (`graph.invoke(inputs, config={"recursion_limit": 5})`), not inside `configurable`; exceeding it
  raises `langgraph.errors.GraphRecursionError`. Set it explicitly for AURORA loops (the
  reasoning loop already has its own iteration cap; keep both bounded).

## 3. Persistence, threads and checkpointers

```python
from langgraph.checkpoint.memory import InMemorySaver   # development only: lost on restart

graph = builder.compile(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": "research-job-<uuid>"}}
result = graph.invoke({"topic": "..."}, config)
```

- A checkpointer persists a thread's state as checkpoints at super-step boundaries; it enables
  resume, human-in-the-loop and fault tolerance. The thread is selected by `thread_id` in
  `config["configurable"]`; reuse the same id to resume, a new id starts a fresh run.
- Production: `PostgresSaver` (`from langgraph.checkpoint.postgres import PostgresSaver`,
  package `langgraph-checkpoint-postgres`, **not** installed in AURORA) with
  `PostgresSaver.from_conn_string(...)` and a one-time `checkpointer.setup()` that creates the
  tables; an async variant `AsyncPostgresSaver` exists. Keep `thread_id` under 255 characters.
  Checkpoints accumulate, so plan a retention/pruning job.
- Security (from the `langgraph-checkpoint-postgres` README): set `LANGGRAPH_STRICT_MSGPACK=true`
  or pass an explicit `allowed_msgpack_modules` list so checkpoint deserialization is limited to
  known-safe types; manual psycopg connections need `autocommit=True` and `row_factory=dict_row`.
- Subgraphs manage their own checkpoint namespace; a parent may not immediately see a subgraph's
  state changes.
- AURORA's research job id is the natural `thread_id`; store the checkpointer's tables in the
  same PostgreSQL instance but treat them as LangGraph-owned (Alembic does not manage them).

## 4. Human-in-the-loop (approval before external writes)

```python
from langgraph.types import Command, interrupt


def approval_node(state) -> Command[Literal["publish", "cancel"]]:
    approved = interrupt({"question": "Publish this draft?", "details": state["draft_summary"]})
    return Command(goto="publish" if approved else "cancel")
```

- `interrupt(value)` pauses the graph; the checkpointer saves state and the graph waits. With
  `invoke()`, the pause is visible as `result["__interrupt__"]`. Resume with
  `graph.invoke(Command(resume=True), config)` on the **same** `thread_id`; the resume value
  becomes the return value of `interrupt()`. Review/edit flows resume with
  `Command(resume="edited text")`.
- Requirements: a checkpointer (durable in production) and a `thread_id`.
- On resume the node **restarts from its beginning**, so code before `interrupt()` runs again:
  make it idempotent (upserts, idempotency keys, read-before-write); prefer side effects after
  the interrupt or in a separate node; deduplicate in the external system anyway.
- Do not wrap `interrupt()` in a bare `try/except` (it catches the internal pause exception);
  keep the number and order of `interrupt()` calls in a node deterministic (resume values match
  by index); pass only simple serializable values; resume only with `Command(resume=...)`, never
  `Command(update=...)` as input.
- For AURORA this is the mandatory gate in Module 7 before WordPress drafts/publishing and any
  Search Console write: the approval decision must also be recorded in PostgreSQL by a node
  after the interrupt.

## 5. Retries, errors and resumable execution

```python
from langgraph.types import RetryPolicy, default_retry_on


def transient_only(exc: BaseException) -> bool:
    from app.tools.errors import ProviderError
    return isinstance(exc, ProviderError) and exc.retryable


builder.add_node(
    "crawl",
    crawl,
    retry_policy=RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2.0, jitter=True, retry_on=transient_only),
)
```

- `RetryPolicy` fields: `max_attempts` (total, incl. the first; default 3), `initial_interval`
  (0.5 s), `backoff_factor` (2.0), `max_interval` (128 s), `jitter` (True), `retry_on`
  (exception type(s) or a predicate; default `default_retry_on`, which does not retry
  `ValueError`, `TypeError`, `OSError`, `RuntimeError` and similar; HTTP clients retry only 5xx).
- Pair this with AURORA's own `retry_async` in the adapters: do not retry the same provider
  call at two layers with large budgets. Keep node-level retries for transient errors only.
- *1.x docs:* `runtime.execution_info.node_attempt` (1-indexed) lets a node fall back after a
  failed attempt; error-handler nodes receive a `NodeError` (`node`, `error`) after retries are
  exhausted and can return `Command(update=..., goto=...)`; `interrupt()` bypasses retries and
  handlers; `NodeTimeoutError` is retryable by default.
- After a failure the failure context is checkpointed. The docs show
  `graph.invoke(None, config)` to continue a run on the same thread after a graceful drain
  (`GraphDrained`, *1.x docs*); verify the exact resume semantics for an unhandled error against
  the installed version before promising "resume from failure" in an API.

## 6. Mapping onto AURORA

| Workflow step | Existing service to call from the node | Notes |
| --- | --- | --- |
| discover | `WebsiteDiscovery` / `SerpApiTool.search` | bounded by `research_max_results` |
| map / crawl | `WebsiteMapper`, `IntelligentCrawler` (Firecrawl adapter) | all URLs via `UrlPolicy`/`SourceManager` |
| extract / validate | `StructuredExtractor`, `EvidenceManager` | deterministic; no LLM required |
| synthesize | `IntelligenceAnalysisService` (Module 4) | uses `TrackedLLM`; budgets apply |
| approve | `interrupt()` node | required before any publish/write (Module 7) |
| publish | `WordPressTool` (skeleton) | only after recorded approval |

Keep `build_*_workflow()` as the single place that imports `langgraph`, compiles the graph and
attaches the checkpointer; services stay framework-free so they remain testable without LangGraph.

## Sources (retrieved 2026-10-31)

- Graph API: https://docs.langchain.com/oss/python/langgraph/graph-api.md
- Persistence: https://docs.langchain.com/oss/python/langgraph/persistence.md
- Interrupts (human-in-the-loop): https://docs.langchain.com/oss/python/langgraph/interrupts.md
- Fault tolerance (retry policies, error handling): https://docs.langchain.com/oss/python/langgraph/fault-tolerance.md
- Durable execution: https://docs.langchain.com/oss/python/langgraph/durable-execution.md (page content overlapped persistence; durability modes were not documented there when fetched)
- Checkpointers index: https://docs.langchain.com/oss/python/langgraph/checkpointers.md (not fetched; listed for follow-up)
- Repository READMEs: https://github.com/langchain-ai/langgraph (root, `libs/langgraph`, `libs/checkpoint-postgres`)
- Full doc index: https://docs.langchain.com (the repository's `docs/llms.txt` lists the Markdown pages)

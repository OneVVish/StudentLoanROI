#!/usr/bin/env python3
"""Guard: a slow research database must never hold up a page.

    python3 check_supabase_resilience.py     (exit 1 on a violation)

Every writer in section 2b runs inside the script run, and the pageview fires
BEFORE the page body renders. st-supabase-connection calls create_client(url,
key) with no options, so the PostgREST client keeps its own default --
DEFAULT_POSTGREST_CLIENT_TIMEOUT, which is 120 SECONDS. One slow response
therefore meant a blank page for two minutes, with nothing on screen to explain
it, and an engaged session makes 6-12 of those calls.

That is the shape a traffic spike takes here: the database throttles first, and
the app looks hung rather than busy. Nothing else in the app can hang -- the
model is ~1ms, a school search ~5ms, a PDF chart ~36ms -- so this file guards
the one path that can.

What it checks, and why each one is here rather than left to care:

  * The configured timeout is small. A version bump that moves
    conn.client.postgrest.session could silently restore the 120s default;
    the code catches that exception so the app keeps working, which means
    nothing would be visibly wrong.
  * A failing transport does not propagate. Every writer is on the render
    path; an exception escaping one takes down the page for that visitor.
  * The breaker opens after N failures and sheds while open. Without it a
    throttling database costs every visitor the timeout on every write --
    the same hang, arriving more slowly.
  * A full queue drops instead of blocking. Blocking on a full queue would
    reintroduce the exact failure this exists to remove, at the worst moment.
  * FIFO order survives. scenario_events.event_seq is assigned on the main
    thread; a pool of workers would deliver rows out of order and make that
    column a lie.

Run after touching section 2b, the queue, or the pinned supabase/postgrest
versions.
"""
import sys
import time

APP = "app.py"

# Longer than a healthy round-trip by a wide margin, shorter than a visitor's
# patience. The real ceiling this exists to keep out is postgrest's own 120.
MAX_ACCEPTABLE_TIMEOUT = 5.0


def load_app_namespace():
    """app.py's sections 1-2, without the UI. Same exec-prefix trick
    analyze_model.py uses -- see CLAUDE.md on why the section banners are
    load-bearing.

    Importantly this must NOT start a writer thread: the queue spawns its
    worker on first submit, never at import, so execing this file stays inert.
    """
    src = open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "supabasecheck"}
    exec(compile(prefix, APP, "exec"), ns)
    return ns


class Boom:
    """A transport that always fails, standing in for an unreachable database."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        raise RuntimeError("database unreachable")


def drain(q, timeout: float = 3.0) -> None:
    """Wait for the worker to finish what it has, without join()ing forever."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if q.stats()["depth"] == 0:
            time.sleep(0.05)          # let the in-flight item finish
            if q.stats()["depth"] == 0:
                return
        time.sleep(0.02)


class _Recorder:
    """A client whose calls are free until .execute(), which is where the real
    HTTP round-trip is and therefore where a failure or a stall belongs."""

    def __init__(self, on_execute=None, seen=None):
        self.on_execute = on_execute
        self.seen = seen if seen is not None else []
        self._rows = []
        self.insert_kwargs = []

    def table(self, name):
        return self

    def insert(self, rows, **kwargs):
        self._rows = rows
        self.insert_kwargs.append(kwargs)
        return self

    def execute(self):
        self.seen.extend(self._rows)
        if self.on_execute:
            self.on_execute()


def _stall():
    """An insert that never returns, standing in for a saturated database."""
    time.sleep(30)


def check_timeout_is_bounded(ns) -> list:
    """The one-line fix, and the reason this file exists."""
    problems = []
    configured = ns.get("SUPABASE_TIMEOUT_SECONDS")
    if not configured:
        return ["  SUPABASE_TIMEOUT_SECONDS is gone -- the client is back on "
                "postgrest's 120-second default"]
    if configured > MAX_ACCEPTABLE_TIMEOUT:
        problems.append(
            f"  SUPABASE_TIMEOUT_SECONDS is {configured}s, above the "
            f"{MAX_ACCEPTABLE_TIMEOUT}s ceiling\n"
            f"    every write is on the render path and the pageview fires "
            f"before the page body")

    # And it must actually be applied to the client, not merely defined. This
    # is the half a version bump breaks: the attribute path is reached through
    # the wrapper, and the app deliberately swallows the failure so the page
    # keeps working -- which is exactly why nothing would look wrong.
    src = open(APP).read()
    if "postgrest.session.timeout" not in src:
        problems.append(
            "  nothing assigns postgrest.session.timeout, so the constant "
            "above is decoration and the client keeps its own default")
    return problems


def check_failures_do_not_propagate(ns) -> list:
    """A write that raises must not take the page with it.

    Both halves, because they fail in different places: the INSERT fails in the
    worker, and building the CLIENT fails on the main thread inside submit().
    An exception escaping either one reaches the render path.
    """
    problems = []

    # 1. The insert fails.
    q = ns["SupabaseWriteQueue"]()
    boom = Boom()
    ns["get_supabase_connection"] = lambda: _Recorder(on_execute=boom)
    try:
        accepted = q.submit("usage_logs", {"action": "pageview"})
    except Exception as error:
        return [f"  submit() raised {error!r} -- it is called on the render "
                f"path and must never raise"]
    if not accepted:
        problems.append("  a healthy queue refused its first row")
    drain(q)
    if q.stats()["failed"] != 1:
        problems.append(f"  an insert failure was not recorded: {q.stats()}")

    # 2. The client cannot even be built.
    q2 = ns["SupabaseWriteQueue"]()
    ns["get_supabase_connection"] = Boom()
    try:
        if q2.submit("usage_logs", {"action": "pageview"}):
            problems.append("  a row was accepted with no usable connection")
    except Exception as error:
        problems.append(
            f"  submit() raised {error!r} when the client could not be built "
            f"-- that happens on the MAIN thread, in front of the visitor")
    if not q2.breaker_is_open():
        problems.append(
            "  an unbuildable client did not open the breaker, so every "
            "later submit retries it on the render path")
    return problems


def check_breaker_opens_and_sheds(ns) -> list:
    """After N consecutive failures, stop paying the timeout for everyone."""
    problems = []
    threshold = ns["SUPABASE_BREAKER_THRESHOLD"]
    # Checked BEFORE it is used as a loop bound, and that ordering is the
    # point: a breaker disabled by raising its threshold instead of deleting it
    # would otherwise make this check submit that many rows and never return --
    # a guard that hangs on the failure it exists to catch. Found exactly that
    # way, by a negative control.
    if not 1 <= threshold <= 10:
        return [f"  SUPABASE_BREAKER_THRESHOLD is {threshold}, outside 1-10\n"
                f"    a threshold this size never trips in a real session, so "
                f"the breaker is off in everything but name"]
    q = ns["SupabaseWriteQueue"]()
    boom = Boom()
    # Failing INSERTS, which is what a throttling database does. A failing
    # connection is covered separately, and takes a different path.
    ns["get_supabase_connection"] = lambda: _Recorder(on_execute=boom)
    for i in range(threshold):
        q.submit("usage_logs", {"i": i})
    drain(q)
    if not q.breaker_is_open():
        problems.append(
            f"  the breaker did not open after {threshold} consecutive "
            f"failures\n    every visitor keeps paying the timeout on every "
            f"write, which is the same hang arriving more slowly")
        return problems

    before = boom.calls
    accepted = q.submit("usage_logs", {"i": "while-open"})
    drain(q)
    if accepted:
        problems.append("  a row was accepted while the breaker was open")
    if boom.calls != before:
        problems.append(
            f"  the transport was called {boom.calls - before} time(s) while "
            f"the breaker was open -- shedding is the whole point")
    if q.stats()["skipped_open"] < 1:
        problems.append("  the skipped row was not counted, so the gap it "
                        "leaves in the data is invisible")
    return problems


def check_full_queue_drops(ns) -> list:
    """A full queue must drop and count, never block."""
    problems = []
    # The stall belongs in the INSERT, not in building the client. That is what
    # a busy database actually looks like, and it is also what the queue is
    # designed around: the connection is resolved once on the main thread, so a
    # fixture that stalls THERE tests a path the design deliberately does not
    # take (and takes minutes to do it).
    ns["get_supabase_connection"] = lambda: _Recorder(on_execute=_stall)

    # The DEFAULT must be bounded, not just the fixture below. This check
    # builds its own small queue to exercise the drop path, so it would happily
    # pass while production ran unbounded -- which is a slow OOM, and an OOM
    # restart is the hang wearing a different hat. Caught by a negative control
    # that changed only the default.
    default_size = ns["SupabaseWriteQueue"]()._queue.maxsize
    if default_size <= 0:
        problems.append(
            "  the default queue is UNBOUNDED, so a stalled database grows it "
            "until the container is killed")
    elif default_size > 5000:
        problems.append(
            f"  the default queue holds {default_size} rows, which is memory "
            f"pretending to be durability -- these are analytics rows")
    q = ns["SupabaseWriteQueue"](maxsize=2)
    # A transport that blocks forever, so the worker cannot drain the queue and
    # the next submits meet a full one.
    accepted = []
    started = time.monotonic()
    for i in range(12):
        accepted.append(q.submit("usage_logs", {"i": i}))
    elapsed = time.monotonic() - started
    if elapsed > 1.0:
        problems.append(
            f"  twelve submits took {elapsed:.1f}s against a stalled database\n"
            f"    submit() is called on the render path and must not block")
    if all(accepted):
        problems.append(
            "  a 2-slot queue accepted twelve rows against a stalled writer, "
            "so it is not bounded")
    if q.stats()["dropped"] < 1:
        problems.append("  rows were refused but not counted as dropped")
    return problems


def check_fifo_order(ns) -> list:
    """event_seq is assigned on the main thread; delivery must keep that order."""
    problems = []
    q = ns["SupabaseWriteQueue"]()
    seen = []
    ns["get_supabase_connection"] = lambda: _Recorder(seen=seen)
    for seq in range(1, 26):
        q.submit("scenario_events", {"event_seq": seq})
    drain(q)
    order = [row["event_seq"] for row in seen]
    if order != sorted(order):
        problems.append(
            f"  rows were delivered out of order: {order[:8]}...\n"
            f"    event_seq is what orders a session's events, and a pool of "
            f"workers would make that column a lie")
    if len(order) != 25:
        problems.append(f"  {25 - len(order)} row(s) never arrived")
    return problems


def check_writers_are_queued(ns) -> list:
    """The two high-frequency writers must not call the database inline.

    Read out of the source: both need session_state to build their rows, so
    calling them here would need a Streamlit runtime. What matters is which
    path they take, and that is visible statically.
    """
    problems = []
    src = open(APP).read()
    for writer in ("def log_usage_event", "def maybe_log_scenario_event"):
        start = src.index(writer)
        # To the next TOP-LEVEL def, not a fixed window: these bodies carry
        # long docstrings, and a window short enough to miss the call reports
        # a queued writer as unqueued.
        nxt = src.find("\ndef ", start + 1)
        body = src[start:nxt if nxt != -1 else len(src)]
        if "get_write_queue()" not in body:
            problems.append(
                f"  {writer.removeprefix('def ')} does not go through the "
                f"write queue\n    it fires before the page body renders (or "
                f"on every rerun) and would put a network round-trip there")
        if "execute_query(" in body:
            problems.append(
                f"  {writer.removeprefix('def ')} still calls execute_query "
                f"inline, so it writes on the render path")
    return problems


def check_inserts_return_minimal(ns) -> list:
    """Every insert asks PostgREST for nothing back.

    postgrest's insert() defaults to returning=representation, which makes the
    INSERT also a SELECT of the row it just wrote. That is invisible while the
    anon role can read the tables, and it is the reason enabling row level
    security with an INSERT-only policy for anon would make every write in
    this app fail -- silently, through the queue, as a permission error on a
    SELECT nobody asked for. The Worker already sends Prefer: return=minimal;
    this makes the app match, so the policy can go in afterwards. Checked on
    the wire for the queued writer (the kwarg the stub receives) and by AST
    for all four call sites, since three of them write synchronously and
    never touch the queue. One negative control.
    """
    problems = []
    q = ns["SupabaseWriteQueue"]()
    rec = _Recorder()
    ns["get_supabase_connection"] = lambda: rec
    q.submit("usage_logs", {"action": "pageview"})
    drain(q)
    if not rec.insert_kwargs or rec.insert_kwargs[0].get("returning") != "minimal":
        problems.append(f"  the queued writer's insert() does not pass "
                        f"returning='minimal': {rec.insert_kwargs}")

    import ast
    src = open(APP).read()

    def unminimal(source):
        bad = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "insert":
                kw = {k.arg: k.value for k in node.keywords}
                v = kw.get("returning")
                if not (isinstance(v, ast.Constant) and v.value == "minimal"):
                    bad.append(node.lineno)
        return bad

    sites = unminimal(src)
    if sites:
        problems.append(f"  .insert( without returning='minimal' at app.py lines "
                        f"{sites}; under an INSERT-only policy these writes fail")
    mutated = src.replace(', returning="minimal")', ")", 1)
    if mutated == src or not unminimal(mutated):
        problems.append("  NEGATIVE CONTROL PASSED: an insert stripped of "
                        "returning='minimal' was not caught")
    return problems


def check_reads_use_reporter(ns) -> list:
    """Readers hold the read-only credential; writers never do.

    Row level security (migrations.sql, 2026-08-30) took SELECT away from
    the anon key the app writes with, so every read has to come through
    get_supabase_read_client and its reporter JWT. The two clients must stay
    separate in both directions: load_table_safe using the writer's
    connection is a dashboard that silently empties the day the policy goes
    in, and a writer using the read client is a credential that can read
    sitting where only inserts should happen. Checked by AST over app.py, plus
    the read client must apply the same timeout the writer does. One negative
    control.
    """
    import ast
    src = open(APP).read()

    def violations(source):
        tree = ast.parse(source)
        bad = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            calls = {n.func.id for n in ast.walk(fn)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            inserts = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                          and n.func.attr == "insert" for n in ast.walk(fn))
            if fn.name == "load_table_safe":
                if "get_supabase_read_client" not in calls:
                    bad.append("load_table_safe does not read through "
                               "get_supabase_read_client")
                if "get_supabase_connection" in calls:
                    bad.append("load_table_safe still uses the WRITER's connection, "
                               "which cannot SELECT under the policy")
            elif inserts and "get_supabase_read_client" in calls:
                bad.append(f"{fn.name} inserts with the read-only client in scope")
        return bad

    problems = [f"  {b}" for b in violations(src)]
    read_fn = src[src.index("def get_supabase_read_client"):]
    read_fn = read_fn[:read_fn.index("\ndef ")]
    if "postgrest.session.timeout" not in read_fn:
        problems.append("  get_supabase_read_client does not apply "
                        "SUPABASE_TIMEOUT_SECONDS; five admin reads at 120s each "
                        "is a page that never renders")
    mutated = src.replace("        client = get_supabase_read_client()\n        if client is None:",
                          "        client = get_supabase_connection()\n        if client is None:", 1)
    if mutated == src or not violations(mutated):
        problems.append("  NEGATIVE CONTROL PASSED: load_table_safe reverted to the "
                        "writer's connection was not caught")
    return problems


def main() -> int:
    ns = load_app_namespace()
    problems, checks = [], []
    for name, fn in [
        ("timeout bounded", check_timeout_is_bounded),
        ("failures contained", check_failures_do_not_propagate),
        ("breaker opens and sheds", check_breaker_opens_and_sheds),
        ("full queue drops", check_full_queue_drops),
        ("FIFO order", check_fifo_order),
        ("writers are queued", check_writers_are_queued),
        ("inserts return minimal", check_inserts_return_minimal),
        ("reads use the reporter key", check_reads_use_reporter),
    ]:
        # A fresh namespace per check: each one monkeypatches the transport,
        # and a leaked stub would make the next check pass for the wrong reason.
        found = fn(load_app_namespace() if name != "timeout bounded" else ns)
        checks.append(name)
        problems += [f"[{name}]\n{p}" for p in found]

    if problems:
        print(f"supabase resilience: {len(problems)} violation(s)\n")
        print("\n\n".join(problems))
        return 1
    print(f"supabase resilience OK -- {len(checks)} properties: writes time out "
          f"at {ns['SUPABASE_TIMEOUT_SECONDS']}s not 120, failures stay off the "
          f"page, the breaker opens after {ns['SUPABASE_BREAKER_THRESHOLD']} and "
          f"sheds, a full queue drops rather than blocks, order survives, "
          f"both high-frequency writers are queued, every insert asks "
          f"for nothing back, and reads go through the read-only client.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

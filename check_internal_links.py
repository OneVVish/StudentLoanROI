#!/usr/bin/env python3
"""Guard: links between this app's own pages must carry the session's flags.

    python3 check_internal_links.py     (exit 1 on a violation)

Cross-links between the standalone tool pages (`?tool=repayment`,
`?tool=schools`) and the calculator are REAL NAVIGATIONS, not reruns. The
browser loads a new page, Streamlit starts a NEW SESSION, and every
`session_state` latch resets. So anything that survives a "Share Scenario" only
BECAUSE it is latched -- `test_mode`, `traffic_source` -- is simply gone on the
other side unless the href itself carries it.

That failed silently in both directions, which is why it needs a check rather
than care:

  * `?test=1` was dropped. A developer on a test session who clicked a
    cross-link landed on a page with no test flag and started writing live rows
    to the production Supabase -- the contamination already on record in
    migrations.sql, reachable in one click. Nothing on screen said so.

  * `?src=` was dropped. A visitor tagged `?src=jefferson_econ` who moved from
    the schools page to the repayment page became untagged for the rest of
    their visit. A wiped tag is NULL exactly as organic traffic is, so it is
    invisible in the data -- and it was biased toward the more engaged
    visitors, since clicking through to a second tool is not a bounce.

`internal_tool_url` builds every internal link. This asserts what it carries
and, just as importantly, what it must NOT.

WHY `src` RIDES AN INTERNAL LINK BUT NOT A SHARE LINK. `session_query_params`
deliberately withholds `src` from share links: a share hands the URL to a
DIFFERENT person, and inheriting the sharer's recruitment channel is fabricated
attribution, worse than the NULL it replaces. A cross-link is the same person
walking between rooms in the same visit. The two rules are consistent; only the
recipient differs. `admin` and `research` stay out of both -- they fail safe
when dropped, and `admin` should never be handed to anyone by a link.

Run after touching `internal_tool_url`, `session_query_params`,
`get_traffic_source`, or `STANDALONE_TOOLS`.
"""
import ast
import re
import sys
from urllib.parse import urlparse, parse_qs

APP = "app.py"


def load_app_namespace():
    """app.py's sections 1-2 plus its later pure functions, without the UI.

    Same exec-prefix trick analyze_model.py and the other guards use -- see
    CLAUDE.md on why the section banners are load-bearing.
    """
    src = open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "linkcheck"}
    exec(compile(prefix, APP, "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), APP, "exec"), ns)
    return ns


class FakeQueryParams(dict):
    """st.query_params outside a runtime. Only .get is exercised."""

    def get(self, key, default=None):
        return dict.get(self, key, default)


def check_landing_action_separate(ns, fail):
    """The edge Worker's landing rows must never count as app pageviews.

    infra/worker.js writes `landing_view:path=...` straight into usage_logs,
    bypassing the app entirely. Those rows have no session_id and never ran a
    Streamlit script -- folding them into PAGEVIEW_ACTIONS would inflate every
    pageview-denominated rate in the admin dashboard AND analyze_survey.py's
    survey-rate denominator, silently, from one date onward. Exactly the
    whole-string-match hazard this file already guards for nav: values.
    """
    prefix = ns["LANDING_ACTION_PREFIX"]
    for action in ns["PAGEVIEW_ACTIONS"]:
        if action.startswith(prefix) or prefix.startswith(action):
            fail(f"PAGEVIEW_ACTIONS contains {action!r}, which collides with "
                 f"the edge landing prefix {prefix!r} -- landings would be "
                 f"counted as app pageviews")
    # And the Worker must actually emit that prefix: a rename on one side only
    # makes the admin panel silently empty forever.
    worker = open("infra/worker.js").read()
    if f'LANDING_ACTION = "{prefix}"' not in worker:
        fail(f"infra/worker.js does not emit LANDING_ACTION = {prefix!r} -- "
             f"app.py's LANDING_ACTION_PREFIX and the Worker have drifted, so "
             f"the admin panel would show nothing with no error anywhere")


def main() -> int:
    ns = load_app_namespace()
    st = ns["st"]
    tools = list(ns["STANDALONE_TOOLS"])

    def url_for(params, tool=""):
        # A fresh session per case: these links are cross-SESSION by nature, so
        # reusing state between cases would test something that cannot happen.
        st.session_state = {}
        st.query_params = FakeQueryParams(params)
        ns["get_traffic_source"]()          # latch, as a real render does
        st.session_state["test_mode"] = params.get("test") == "1"
        return ns["internal_tool_url"](tool)

    problems, checked = [], 0

    def check(label, url, must_have, must_not):
        nonlocal checked
        checked += 1
        q = parse_qs(urlparse(url).query)
        missing = [k for k in must_have if k not in q]
        leaked = [k for k in must_not if k in q]
        if missing or leaked:
            detail = []
            if missing:
                detail.append(f"missing {missing}")
            if leaked:
                detail.append(f"LEAKED {leaked}")
            problems.append(f"  {label}\n      {url}\n      " + "; ".join(detail))

    # 1. Nothing invented on a clean visit.
    check("clean visit -> calculator", url_for({}),
          [], ["test", "src", "tool", "admin", "research"])
    for tool in tools:
        check(f"clean visit -> {tool}", url_for({}, tool),
              ["tool"], ["test", "src"])

    # 2. test must reach EVERY tool page. Losing it is the production-write bug.
    for tool in tools:
        check(f"?test=1 -> {tool}", url_for({"test": "1"}, tool),
              ["test", "tool"], ["src"])
    check("?test=1 -> calculator", url_for({"test": "1"}),
          ["test"], ["tool", "src"])

    # 3. src must reach every tool page, and back to the calculator.
    for tool in tools:
        check(f"?src= -> {tool}", url_for({"src": "jefferson_econ"}, tool),
              ["src", "tool"], ["test"])
    check("?src= -> calculator", url_for({"src": "jefferson_econ"}),
          ["src"], ["tool", "test"])

    # 4. Both together, which is the real developer-testing case.
    for tool in tools:
        check(f"?test=1&src= -> {tool}",
              url_for({"test": "1", "src": "jefferson_econ"}, tool),
              ["test", "src", "tool"], [])

    # 5. admin and research must NEVER ride along. Both fail safe when dropped,
    #    and handing an admin flag to whoever clicks a link is not a default
    #    anyone should have to notice.
    for tool in tools:
        check(f"admin/research excluded -> {tool}",
              url_for({"admin": "1", "research": "1", "src": "x"}, tool),
              ["src", "tool"], ["admin", "research"])

    # 6. The VALUE must survive, not merely the key -- including one that needs
    #    URL escaping. A tag mangled in transit is as useless as one dropped,
    #    and far more confusing in the data.
    for raw in ("jefferson_econ", "hs counselor/spring 2026", "a&b=c"):
        checked += 1
        url = url_for({"src": raw}, tools[0] if tools else "")
        got = parse_qs(urlparse(url).query).get("src", [None])[0]
        if got != raw:
            problems.append(
                f"  src value mangled in transit\n      sent {raw!r}, "
                f"link carries {got!r}\n      {url}")

    # 7. Every link must say where it was clicked FROM, and that origin must be
    #    a member of NAV_ORIGINS -- the landing validates against that set
    #    before writing a nav: event, so a link carrying anything else produces
    #    no event at all and the transition is silently lost.
    origins = ns["NAV_ORIGINS"]
    for page in ["", *tools]:
        for dest in ["", *tools]:
            checked += 1
            st.session_state = {}
            st.query_params = FakeQueryParams({})
            ns["get_traffic_source"]()
            st.session_state["active_tool"] = page      # "" == the calculator
            url = ns["internal_tool_url"](dest)
            got = parse_qs(urlparse(url).query).get("from", [None])[0]
            want = page or "calculator"
            if got != want:
                problems.append(
                    f"  link from {want!r} to {dest or 'calculator'!r} carries "
                    f"from={got!r}\n      {url}")
            elif got not in origins:
                problems.append(
                    f"  from={got!r} is not in NAV_ORIGINS, so the landing will "
                    f"discard it and the transition is lost\n      {url}")

    # 8. nav_action must REFUSE anything outside the known set. An unvalidated
    #    ?from= would let a hand-edited URL write arbitrary text into
    #    usage_logs.action, which is the research dataset.
    for bogus in ("bogus", "", "calculator; drop", "../admin", "Calculator"):
        checked += 1
        if ns["nav_action"](bogus, "repayment") != "":
            problems.append(
                f"  nav_action accepted an unknown origin {bogus!r} -- a "
                f"hand-edited URL could inject it into the action stream")
    checked += 1
    if ns["nav_action"]("calculator", "bogus") != "":
        problems.append("  nav_action accepted an unknown DESTINATION")

    # 9. The shapes the admin table's parser and analyze_survey both rely on.
    checked += 1
    if ns["nav_action"]("calculator", "repayment") != "nav:from=calculator:to=repayment":
        problems.append(f"  page-navigation shape changed: "
                        f"{ns['nav_action']('calculator', 'repayment')!r}")
    checked += 1
    if ns["nav_action"]("schools", "calculator", inpage=True) != \
            "nav:from=schools:to=calculator:inpage=1":
        problems.append(f"  in-page shape changed: "
                        f"{ns['nav_action']('schools', 'calculator', inpage=True)!r}")

    # 10. A nav event must never collide with a landing action. Five readers
    #     match those whole strings exactly; a nav that looked like one would
    #     silently inflate the landing counts.
    for a, b in [(o, d) for o in origins for d in origins]:
        act = ns["nav_action"](a, b)
        checked += 1
        if act in ns["PAGEVIEW_ACTIONS"]:
            problems.append(f"  nav_action produced {act!r}, which is also a "
                            f"landing action -- it would be counted as a visit")

    # 11. Every registered tool must be reachable by its own key, or a page
    #     exists that nothing links to.
    for tool in tools:
        checked += 1
        url = url_for({}, tool)
        if parse_qs(urlparse(url).query).get("tool", [None])[0] != tool:
            problems.append(f"  {tool!r} is in STANDALONE_TOOLS but its link "
                            f"does not carry ?tool={tool}\n      {url}")

    # 12. Every registered tool must have a RENDERER the dispatch reaches.
    #
    #     The registry hands a new tool its action, traffic split, admin row
    #     and cross-links automatically -- everything except the one thing it
    #     cannot derive, which is the `elif active_tool == "<key>":` branch and
    #     the function behind it. Register a key and forget the branch and the
    #     page still resolves, still logs a pageview, still renders its title
    #     and caption from the registry, and then shows nothing. A blank page
    #     under a heading, no error anywhere.
    #
    #     Read out of the source rather than executed: the dispatch lives in
    #     section 5 and cannot run without a Streamlit session, and the
    #     renderers are defined ABOVE it precisely so a def below its caller
    #     cannot NameError at runtime -- which py_compile also cannot see.
    dispatch = re.findall(r'active_tool\s*==\s*["\'](\w+)["\']\s*:\s*\n\s*(\w+)\(',
                          open(APP).read())
    dispatched = {key: fn for key, fn in dispatch}
    for tool in tools:
        checked += 1
        renderer = dispatched.get(tool)
        if renderer is None:
            problems.append(
                f"  {tool!r} is in STANDALONE_TOOLS with no dispatch branch\n"
                f"      ?tool={tool} would render its title and caption and then "
                f"stop -- a blank page, and nothing raises")
        elif not callable(ns.get(renderer)):
            problems.append(
                f"  {tool!r} dispatches to {renderer}(), which is not a function "
                f"in app.py's module scope\n"
                f"      a renderer defined below the dispatch is a NameError only "
                f"the live page can show you")

    checked += 1
    check_landing_action_separate(
        ns, lambda msg: problems.append("  " + msg))

    if problems:
        print(f"internal links: {len(problems)} problem(s) across {checked} checks\n")
        print("\n\n".join(problems))
        return 1
    print(f"internal links OK -- {checked} checks across "
          f"{len(tools)} standalone tool(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

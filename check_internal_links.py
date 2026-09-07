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
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent

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


def check_landing_ctas_tagged(ns, fail):
    """Every app-bound link on the welcome page must carry from=welcome.

    That param is the whole per-destination breakdown: the app validates it
    against NAV_ORIGINS and logs `nav:from=welcome:to=X`. A link that loses it
    still works perfectly for the visitor and silently stops being counted --
    the admin panel just shows fewer clicks and a bigger "no click" estimate,
    with nothing anywhere to say a link went dark.
    """
    import re as _re
    html = open("infra/landing.html").read()
    if ns["NAV_WELCOME"] not in ns["NAV_ORIGINS"]:
        fail(f"{ns['NAV_WELCOME']!r} is not in NAV_ORIGINS -- nav_action would "
             f"reject every welcome click and return \"\", so the landing's "
             f"CTAs would log nothing")
    for href in _re.findall(r'href="(/[^"]*)"', html):
        # Four kinds of link need no origin tag:
        #   * bare "/" points AT this page;
        #   * /guides/* are EDGE pages -- the app never sees the request, so
        #     from= would be meaningless. The Worker logs those reads itself
        #     as guide_view rows;
        #   * /charts is an edge page for the same reason. Its own CTAs into
        #     the app DO carry from=charts, which is where that hop is
        #     counted; tagging the link INTO it would claim a nav the app
        #     never sees;
        #   * llms.txt leaves the site entirely.
        #   * /welcome is this page by its other name, and the only name that
        #     survives a carried query string (see check_logo_goes_to_welcome).
        if (href == "/" or href == "/welcome" or href.startswith("/guides")
                or href.startswith("/charts") or "llms.txt" in href):
            continue
        if f"from={ns['NAV_WELCOME']}" not in href:
            fail(f"landing CTA {href!r} does not carry from={ns['NAV_WELCOME']} "
                 f"-- clicks through it will not be counted")


def check_logo_goes_to_welcome(fail):
    """The header mark on every edge page must link to /welcome, never "/".

    The Worker serves the landing page for a bare "/" and the Streamlit app
    for "/" with ANY query string, and CARRY_QS_JS appends the visitor's own
    query string to every internal link. So a logo pointing at "/" sends a
    visitor who arrived with ?src=, ?test=1 or ?from= straight into the app,
    with no way back to the edge site from any guide or the gallery. Reported
    2026-09-05 as "no way to navigate from the guides page back to
    worthmydegree.com". /welcome is the landing page under a name the Worker
    honours whatever the query string carries, and the tag rides along.
    """
    import glob
    pages = ["infra/landing.html"] + sorted(glob.glob("infra/guides/*.html"))
    for page in pages:
        text = Path(page).read_text()
        if 'class="logo"' not in text:
            continue
        if 'class="logo" href="/welcome"' not in text:
            fail(f"{page}: the header logo does not link to /welcome, so a "
                 f"carried query string lands it on the app instead")


def check_landing_action_separate(ns, fail):
    """The edge Worker's landing rows must never count as app pageviews.

    infra/worker.js writes `landing_view:path=...` straight into usage_logs,
    bypassing the app entirely. Those rows have no session_id and never ran a
    Streamlit script -- folding them into PAGEVIEW_ACTIONS would inflate every
    pageview-denominated rate in the admin dashboard AND analyze_survey.py's
    survey-rate denominator, silently, from one date onward. Exactly the
    whole-string-match hazard this file already guards for nav: values.
    """
    for prefix in ns["EDGE_ACTION_PREFIXES"]:
        for action in ns["PAGEVIEW_ACTIONS"]:
            if action.startswith(prefix) or prefix.startswith(action):
                fail(f"PAGEVIEW_ACTIONS contains {action!r}, which collides "
                     f"with the edge prefix {prefix!r} -- edge rows would be "
                     f"counted as app pageviews")
    prefix = ns["LANDING_ACTION_PREFIX"]
    # And the Worker must actually emit that prefix: a rename on one side only
    # makes the admin panel silently empty forever.
    worker = open("infra/worker.js").read()
    if f'GUIDE_ACTION = "{ns["GUIDE_ACTION_PREFIX"]}"' not in worker:
        fail(f"infra/worker.js does not emit GUIDE_ACTION = "
             f"{ns['GUIDE_ACTION_PREFIX']!r} -- guide reads would stop being "
             f"counted and the like column would lose its denominator")
    if f'LIKE_ACTION = "{ns["LIKE_ACTION_PREFIX"]}"' not in worker:
        fail(f"infra/worker.js does not emit LIKE_ACTION = "
             f"{ns['LIKE_ACTION_PREFIX']!r} -- the guide-reactions panel "
             f"would stay empty with no error anywhere")
    if f'SHARE_ACTION = "{ns["SHARE_ACTION_PREFIX"]}"' not in worker:
        fail(f"infra/worker.js does not emit SHARE_ACTION = "
             f"{ns['SHARE_ACTION_PREFIX']!r} -- the Shares column would read "
             f"zero for every guide, which is exactly what a guide nobody "
             f"shares looks like")
    # And the article pages must POST to the endpoint the Worker answers on. A
    # button that fires into a 404 still confirms to the reader, so the only
    # symptom is a column that never moves.
    if '"/api/share"' not in worker:
        fail("infra/worker.js has no /api/share route -- every share POST "
             "would fall through to the app origin and be lost")
    guides = open("infra/build_site.py").read()
    if 'fetch("/api/share"' not in guides:
        fail("infra/build_site.py does not POST to /api/share -- the Share "
             "button would work perfectly and count nothing")
    if f'LANDING_ACTION = "{prefix}"' not in worker:
        fail(f"infra/worker.js does not emit LANDING_ACTION = {prefix!r} -- "
             f"app.py's LANDING_ACTION_PREFIX and the Worker have drifted, so "
             f"the admin panel would show nothing with no error anywhere")


def check_repayment_section_guides(ns, src):
    """Every per-section guide pointer names a guide that is actually published.

    The repayment tool carries one further-reading line per result section.
    A link to a guide that has been renamed or unpublished 404s SILENTLY: the
    page renders, the caption reads correctly, and only a click finds it. So
    the slugs are checked against content/posts/ rather than trusted.

    Also asserts the three things that make the mechanism safe:

      - every key passed at a call site is in the registry, because
        repayment_section_guide returns "" for an unknown key and a typo
        would cost the line with nothing failing;
      - no guide is registered twice, which is the "reads as furniture"
        objection the registry's own comment records;
      - the link is built by guides_url and never as a bare /guides/ path,
        because this app answers on two hosts and only one of them serves the
        guides.
    """
    out = []
    reg = ns["REPAYMENT_SECTION_GUIDES"]
    posts = {p.stem for p in (ROOT / "content" / "posts").glob("*.md")}

    for key, (slug, blurb) in reg.items():
        if slug not in posts:
            out.append(f"  {key!r} points at {slug!r}, which is not a published "
                       f"guide in content/posts/")
        if not blurb or blurb.endswith("."):
            out.append(f"  {key!r}'s blurb should be a fragment without a final "
                       f"period; the renderer adds one")

    slugs = [slug for slug, _ in reg.values()]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    if dupes:
        out.append(f"  the same guide is registered for more than one section: "
                   f"{sorted(dupes)}. One guide per section, or the pointers "
                   f"read as furniture rather than as further reading")

    used = set(re.findall(r'repayment_section_guide\("([a-z_]+)"\)', src))
    unknown = used - set(reg)
    if unknown:
        out.append(f"  called with unregistered key(s) {sorted(unknown)}; "
                   f"repayment_section_guide returns \"\" for those, so the "
                   f"line silently disappears")
    unused = set(reg) - used
    if unused:
        out.append(f"  registered but never rendered: {sorted(unused)}")

    for key in used & set(reg):
        link = ns["repayment_section_guide"](key)
        if "/guides/" not in link or not link.startswith("\U0001f4d6 [Further reading](http"):
            out.append(f"  {key!r} does not render an absolute guides_url link: "
                       f"{link!r}. A bare path resolves against whichever host "
                       f"is serving, and only one of the two serves /guides")
    return out


def main() -> int:
    ns = load_app_namespace()
    st = ns["st"]
    tools = list(ns["STANDALONE_TOOLS"])

    def url_for(params, tool="", profile=None, extra=None):
        # A fresh session per case: these links are cross-SESSION by nature, so
        # reusing state between cases would test something that cannot happen.
        st.session_state = {}
        st.query_params = FakeQueryParams(params)
        ns["get_traffic_source"]()          # latch, as a real render does
        st.session_state["test_mode"] = params.get("test") == "1"
        if profile is not None:
            st.session_state["_profile_params"] = profile
        return ns["internal_tool_url"](tool, extra=extra)

    problems, checked = [], 0
    worker = (ROOT / "infra" / "worker.js").read_text()
    src = (ROOT / "app.py").read_text()
    found = check_repayment_section_guides(ns, src)
    checked += 1
    if found:
        problems.append("repayment section guides:\n" + "\n".join(found))

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

    # 5b. THE PROFILE RIDES EVERY LINK, and cannot smuggle a flag. The scenario
    #     the sidebar holds is stamped into session_state["_profile_params"]
    #     and every cross-link carries it; before 2026-09-06 a tool link was a
    #     new session on the default scenario. Session flags outrank it, an
    #     `extra` (the wizard's answers) overrides it, and a profile can never
    #     carry admin, research, or a tool of its own.
    profile = {"major": "Nursing", "city": "Austin, TX", "school": "Rice University",
               "tool": "repayment", "admin": "1", "research": "1", "test": "0"}
    for tool in tools + [""]:
        url = url_for({"test": "1"}, tool, profile=profile)
        q = parse_qs(urlparse(url).query)
        check(f"profile rides -> {tool or 'calculator'}", url,
              ["major", "city", "school", "test"], ["admin", "research"])
        checked += 1
        if q.get("test") != ["1"] or q.get("tool", [""])[0] != tool:
            problems.append(f"  profile outranked a session flag or the tool\n      {url}")
    url = url_for({}, "", profile=profile, extra={"major": "Physics", "compare": "0"})
    q = parse_qs(urlparse(url).query)
    checked += 1
    if q.get("major") != ["Physics"] or q.get("compare") != ["0"] or q.get("city") != ["Austin, TX"]:
        problems.append(f"  extra did not override the profile\n      {url}")
    checked += 1
    if "major" in parse_qs(urlparse(url_for({}, "schools")).query):
        problems.append("  a link with no profile invented one")

    # 6. The VALUE must survive, not merely the key. A tag mangled in transit
    #    is as useless as one dropped, and far more confusing in the data.
    #    These are real tag shapes: a word, an all-caps college abbreviation,
    #    a hyphenated chart stem. (Two earlier fixtures, "hs counselor/spring
    #    2026" and "a&b=c", tested URL escaping with values that are no longer
    #    tags at all; they moved to 6b, where they must come back as None.)
    for raw in ("jefferson_econ", "LACC", "transfer-path", "bak16"):
        checked += 1
        url = url_for({"src": raw}, tools[0] if tools else "")
        got = parse_qs(urlparse(url).query).get("src", [None])[0]
        if got != raw:
            problems.append(
                f"  src value mangled in transit\n      sent {raw!r}, "
                f"link carries {got!r}\n      {url}")

    # 6b. A value that is not a tag must latch as None and ride no link.
    #     traffic_source is unbounded text on every row of every table, and
    #     both the app and the edge Worker wrote whatever the URL carried: a
    #     GET on a guide with a 20 KB ?src= stored the 20 KB. None is what an
    #     untagged visit always was, so nothing downstream changes meaning.
    normalize = ns["normalize_traffic_source"]
    for raw in ("hs counselor/spring 2026", "a&b=c", "x" * 41, "x" * 20_000,
                "<script>alert(1)</script>", "", "tag.with.dots", "ünïcode"):
        checked += 1
        if normalize(raw) is not None:
            problems.append(f"  normalize_traffic_source accepted {raw[:40]!r}")
        url = url_for({"src": raw}, tools[0] if tools else "")
        if "src" in parse_qs(urlparse(url).query):
            problems.append(f"  a non-tag src {raw[:40]!r} rode an internal link")
        if st.session_state.get("traffic_source") is not None:
            problems.append(f"  a non-tag src {raw[:40]!r} latched as "
                            f"{st.session_state.get('traffic_source')!r}, not None")

    # 6c. Every tag actually in use must pass. The taxonomy lives in
    #     marketing/README.md (gitignored, so SKIPPED LOUDLY on a clone), the
    #     per-chart tags are the manifests' filename stems, and four are
    #     constants the code itself compares against.
    tags = {"selftest", "img", "poster", "reddit", "jefferson_econ"}
    tags |= {p.stem for p in (ROOT / "content" / "charts").glob("*.md")
             if not p.name.startswith("_")}
    taxonomy = ROOT / "marketing" / "README.md"
    if taxonomy.exists():
        for line in taxonomy.read_text().splitlines():
            if line.startswith("| `") or line.startswith("| ~~`"):
                cell = line.split("|")[1]
                tags |= set(re.findall(r"`([^`]+)`", cell))
    else:
        print("  NOTE: marketing/README.md is absent (gitignored); the src "
              "taxonomy was not checked against the tag rule")
    js_re = re.search(r"^const SRC_TAG_RE = /(.*)/;$", worker, re.M)
    for tag in sorted(tags):
        checked += 1
        if normalize(tag) != tag:
            problems.append(f"  tag {tag!r} is in use and normalize_traffic_source "
                            f"rejects it; every visit so tagged would log as untagged")
        if js_re and not re.match(js_re.group(1), tag):
            problems.append(f"  tag {tag!r} is in use and the Worker's SRC_TAG_RE "
                            f"rejects it")

    # 6d. The Worker's pattern must be the app's pattern, character for
    #     character: two rules is how a tag gets stored from one door and
    #     dropped at the other.
    checked += 1
    py_re = ns["TRAFFIC_SOURCE_RE"].pattern
    if not js_re:
        problems.append("  infra/worker.js has no SRC_TAG_RE; edge rows take any ?src=")
    elif js_re.group(1) != py_re:
        problems.append(f"  SRC_TAG_RE {js_re.group(1)!r} differs from "
                        f"TRAFFIC_SOURCE_RE {py_re!r}")

    # 6e. Every edge row's traffic_source goes through srcTag(). Checked as
    #     text, since the Worker is JavaScript: each `traffic_source:` in the
    #     file must be `traffic_source: srcTag(url)`.
    def edge_sites_guarded(text):
        sites = re.findall(r"traffic_source:\s*([^,\n]+)", text)
        return bool(sites) and all(v.strip() == "srcTag(url)" for v in sites)
    checked += 1
    if not edge_sites_guarded(worker):
        problems.append("  infra/worker.js writes traffic_source from somewhere "
                        "other than srcTag(url)")
    # Negative controls: one site reverted to the raw param must fail 6e, and
    # a loosened Worker pattern must fail 6d.
    checked += 2
    reverted = worker.replace("traffic_source: srcTag(url)",
                              'traffic_source: url.searchParams.get("src") || null', 1)
    if reverted == worker or edge_sites_guarded(reverted):
        problems.append("  NEGATIVE CONTROL PASSED: an unguarded traffic_source "
                        "site in worker.js was not caught")
    loosened = re.search(r"^const SRC_TAG_RE = /(.*)/;$",
                         worker.replace("{1,40}", "{1,}", 1), re.M)
    if loosened and loosened.group(1) == py_re:
        problems.append("  NEGATIVE CONTROL PASSED: a loosened SRC_TAG_RE still "
                        "matched TRAFFIC_SOURCE_RE")

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

    checked += 2
    _fail = lambda msg: problems.append("  " + msg)
    check_landing_action_separate(ns, _fail)
    check_landing_ctas_tagged(ns, _fail)
    check_logo_goes_to_welcome(_fail)

    if problems:
        print(f"internal links: {len(problems)} problem(s) across {checked} checks\n")
        print("\n\n".join(problems))
        return 1
    print(f"internal links OK -- {checked} checks across "
          f"{len(tools)} standalone tool(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

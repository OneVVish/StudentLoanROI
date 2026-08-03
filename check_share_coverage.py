#!/usr/bin/env python3
"""Guard: every sidebar input must round-trip through a Share Scenario link.

Adding a sidebar widget has a silent failure mode. If you don't also wire it
into `build_share_params` and read it back with a `get_shared_*` getter, shared
links quietly recreate every field EXCEPT the new one, and nothing errors --
no exception, no warning, no visibly wrong number. The link just rebuilds a
different scenario than the one that was shared.

That is not hypothetical. The returning-student mode shipped seven sidebar
inputs (student mode, age, current salary, expected salary without the degree,
existing debt and its rate, enrollment choice, salary override) with none of
them in `build_share_params`. Every link built from a returning-student
session -- including one measuring a 49-year-old against her own salary --
reloaded as a first-time student compared against a debt-free 18-year-old.
Same school, same major, a different question, no sign anything was lost.

Run it:  python3 check_share_coverage.py     (exit 1 on an uncovered input)

Two independent checks, because the two halves fail separately:

  1. READ SIDE  -- every non-exempt widget `key=` is seeded from a shared
     param: a `get_shared_*` getter (inline in the widget call, or via a
     `setdefault`), or an `apply_shared_flag("param", "key")` call.
  2. EMIT SIDE  -- every param name any `get_shared_*` call reads is actually
     emitted by `build_share_params`. Catches the reverse mistake: a getter
     added, the emit forgotten, so the link never carries what it reads.

This is a static check. It proves a field is wired into both halves, not that
it round-trips to the right VALUE -- a re-pin that fires on first render can
still overwrite a seeded value afterwards (see `_salary_override_major` and
`_city_school` in app.py, both of which exist to stop exactly that). Those
need a browser check on a real link.
"""
import ast
import sys

APP = "app.py"

WIDGET_FUNCS = {
    "radio", "selectbox", "number_input", "slider", "text_input",
    "checkbox", "multiselect", "toggle", "select_slider", "text_area",
}

# Inputs that deliberately do NOT ride in a share link. Every entry needs a
# reason: the point of the allowlist is to force the decision to be conscious,
# not to be a place to silence the check.
SHARE_EXEMPT = {
    # Survey answers -- a research instrument, not part of a scenario. Sharing
    # them would put one visitor's answers in another visitor's form.
    "presurvey_role": "survey answer, not a scenario field",
    "presurvey_schools": "survey answer",
    "presurvey_borrowing": "survey answer",
    "presurvey_age_ok": "age attestation, must be answered by whoever is here",
    "survey_age_gate": "age attestation",
    "survey_role": "survey answer",
    "survey_helpful": "survey answer",
    "survey_changed_mind": "survey answer",
    "survey_confidence": "survey answer",
    "survey_comments": "survey answer",
    "survey_email": "survey answer, and personal data",
    # Transient search UI. The budget-first search is a way of ARRIVING at a
    # school; once one is picked, `school` carries the result and re-running
    # someone else's search on their budget is not what a shared link is for.
    "search_budget": "transient school-search input; ?school= carries the result",
    "search_coa_range": "transient school-search input; ?school= carries the result",
    "search_credential": "transient school-search input",
    "search_states": "transient school-search input",
    "search_pick": "transient school-search result picker",
    "search_home_state": "transient school-search input; drives in-state pricing "
                         "for the search only, and ?in_state= carries the result",
    "school_search_a": "raw search text; ?school= carries the resolved name",
    "school_search_b": "raw search text; ?school_b= carries the resolved name",
    "school_pick_a": "disambiguation picker; ?school= carries the resolved name",
    "school_pick_b": "disambiguation picker; ?school_b= carries the resolved name",
    # The existing-loan comparison. A separate question from the scenario --
    # what to do about debt you already have, not whether to take it on -- so
    # these are not scenario fields and a shared link has nothing to say about
    # them. Deliberately NOT round-tripped for a second reason: a balance and
    # an income are the most identifying numbers a visitor can type, and a
    # share link is a URL that gets pasted into chats and emails.
    "existing_balance": "existing-loan comparison; not a scenario field, and personal",
    "existing_rate": "existing-loan comparison; not a scenario field",
    "existing_accrued_interest": "existing-loan comparison; not a scenario field, and personal",
    "existing_income": "existing-loan comparison; not a scenario field, and personal",
    "existing_dependents": "existing-loan comparison; not a scenario field",
    "existing_forgivable": "existing-loan comparison; not a scenario field",
    "existing_chart_plan": "which plan's chart to view; a view control, not an input",
    "existing_pslf": "existing-loan comparison; not a scenario field",
    "existing_prior_payments": "existing-loan comparison; not a scenario field, "
                               "and a payment history is personal",
    # Display-only / derived.
    "loan_mode_unavailable_display": "read-only display of a forced value",
}

# Query params read by get_shared_* that are not scenario fields, so
# build_share_params correctly does not emit them.
PARAM_EXEMPT = {
    "tz": "browser-detected timezone, set by JS on arrival; a browser fact, "
          "not a scenario field. get_user_timezone latches it into "
          "session_state so a share replacing the query string cannot wipe it",
    "test": "test-mode flag; carried separately by session_query_params()",
    "admin": "admin reveal; deliberately not propagated by a share link",
    "src": "traffic source; a share link must not inherit the sharer's source",
    "research": "survey-enable flag; deliberately excluded from share links",
    "tool": "page-mode flag (?tool=repayment / ?tool=schools); a shared SCENARIO "
            "must not drag the recipient onto a different tool",
    "cc_a": "legacy boolean, superseded by cc_mode_a; read for old links only",
    "cc_b": "legacy boolean, superseded by cc_mode_b; read for old links only",
}


def _shared_locals(tree) -> set:
    """Names assigned from an expression containing a get_shared_* call.

    The codebase seeds widgets both ways -- inline
    (`value=get_shared_float("rate", ...)`) and via a local first
    (`shared_city = get_shared_default("city", ...)`, used further down), and
    chains them up to three deep, so this iterates to a fixpoint.

    It is reachability, not real dataflow: a name assigned from an expression
    that merely MENTIONS a shared local counts as shared. That direction is
    deliberate -- it under-reports rather than crying wolf, and a guard nobody
    trusts gets switched off. The check earns its keep on inputs wired to
    nothing at all, which is how every real instance has looked.
    """
    assigns = [(n.targets, n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    names = set()
    # Iterate to a fixpoint: app.py chains these up to three deep
    # (`_shared_compare` -> `_default_compare` -> setdefault("compare_mode")),
    # so a single pass would report a covered input as uncovered.
    changed = True
    while changed:
        changed = False
        for targets, value in assigns:
            if not (_has_getter_call(value) or _reads_any(value, names)):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    changed = True
    return names


def _reads_any(node, names) -> bool:
    return any(isinstance(sub, ast.Name) and sub.id in names for sub in ast.walk(node))


def _has_getter_call(node) -> bool:
    """True if any get_shared_* call appears anywhere in this subtree."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name.startswith("get_shared_"):
                return True
    return False


def _contains_shared_getter(node, shared_names) -> bool:
    """True if this subtree calls a get_shared_* getter directly, or reads a
    local that was itself assigned from one."""
    return _has_getter_call(node) or _reads_any(node, shared_names)


def main() -> int:
    src = open(APP).read()
    tree = ast.parse(src)
    shared_names = _shared_locals(tree)

    widget_keys = {}      # key -> covered inline by a getter in the widget call
    setdefault_cov = {}   # key -> covered by a setdefault or an assignment
    read_params = set()   # param names any get_shared_* reads
    emitted = set()       # param names build_share_params writes

    # `st.session_state["key"] = <something derived from a shared param>` --
    # the third seeding form, used where the value needs a cast or a guard
    # (`if shared_coa_a is not None: st.session_state["coa_per_year_a"] = ...`)
    # and so can't be expressed as a setdefault.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not _contains_shared_getter(node.value, shared_names):
            continue
        for target in node.targets:
            if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                k = target.slice.value
                if isinstance(k, str):
                    setdefault_cov[k] = True
            elif isinstance(target, ast.Attribute):
                setdefault_cov[target.attr] = True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")

        if name in WIDGET_FUNCS:
            for kw in node.keywords:
                if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                    k = kw.value.value
                    widget_keys[k] = widget_keys.get(k, False) or _contains_shared_getter(node, shared_names)

        elif name == "setdefault" and node.args and isinstance(node.args[0], ast.Constant):
            k = node.args[0].value
            if isinstance(k, str):
                covered = any(_contains_shared_getter(a, shared_names) for a in node.args[1:])
                setdefault_cov[k] = setdefault_cov.get(k, False) or covered

        elif name.startswith("get_shared_") and node.args:
            if isinstance(node.args[0], ast.Constant):
                read_params.add(node.args[0].value)

        # apply_shared_flag("param", "session_key") is the OTHER seeding
        # mechanism -- it reads st.query_params directly rather than through a
        # get_shared_* getter, because it has to detect a value CHANGE to
        # survive a same-tab URL edit. It seeds its key and reads its param
        # just as truly, so the check must count both or it reports five wired
        # inputs as unwired.
        elif name == "apply_shared_flag" and len(node.args) >= 2:
            if all(isinstance(a, ast.Constant) for a in node.args[:2]):
                read_params.add(node.args[0].value)
                setdefault_cov[node.args[1].value] = True

    # Every string key build_share_params puts in its dict, whether via the
    # literal, a params[...] assignment, or a .update({...}).
    fnode = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "build_share_params")
    for sub in ast.walk(fnode):
        if isinstance(sub, ast.Dict):
            emitted.update(k.value for k in sub.keys
                           if isinstance(k, ast.Constant) and isinstance(k.value, str))
        elif isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Constant):
            emitted.add(sub.slice.value)

    failures = []

    for key in sorted(widget_keys):
        if key in SHARE_EXEMPT:
            continue
        if widget_keys[key] or setdefault_cov.get(key):
            continue
        failures.append(
            f"  READ SIDE  sidebar input {key!r} is never seeded from a shared link.\n"
            f"             Add a get_shared_* call to its widget or its setdefault,\n"
            f"             emit it from build_share_params -- or add it to\n"
            f"             SHARE_EXEMPT here with a reason."
        )

    for param in sorted(read_params - emitted - set(PARAM_EXEMPT)):
        failures.append(
            f"  EMIT SIDE  ?{param}= is read by a get_shared_* call but never\n"
            f"             emitted by build_share_params, so no share link can\n"
            f"             ever carry it."
        )

    checked = len(widget_keys) - len([k for k in widget_keys if k in SHARE_EXEMPT])
    if failures:
        print(f"share-link coverage: {len(failures)} problem(s)\n")
        print("\n\n".join(failures))
        return 1

    print(f"share-link coverage OK -- {checked} sidebar inputs round-trip, "
          f"{len(SHARE_EXEMPT)} exempt, {len(emitted)} params emitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

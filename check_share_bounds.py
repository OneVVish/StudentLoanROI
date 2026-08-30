#!/usr/bin/env python3
"""Guard: a number read off a share link lands on its widget's bound, not past it.

Streamlit does NOT raise when session_state holds a value outside a
number_input's min_value/max_value. It discards the value and renders the
widget's default, which for a keyed input with no value= is the MINIMUM. So a
hand-edited ?age=99999 opened as a returning student aged 18, and so did
?age=85 -- the nearest legal answer was 80 and the page said 18, with nothing
on screen to show the link had been overruled. Verified 2026-08-30 under
AppTest on the pinned 1.58.0; the number_input source checks only the value=
argument against the bounds, never the stored state.

The fix is `get_shared_int(param, fallback, lo=, hi=)` (and the float twin),
which clamp a parsed value to the widget's own bounds. This guard makes the
kwargs mandatory wherever a seed feeds a bounded widget, and makes them TRUE:
a seed that names the wrong bound would clamp to a value the widget then
discards, which is the original bug wearing a fix.

    python3 check_share_bounds.py        exit 1 on any failure

What it checks, all by AST over app.py, because the seeds live in section 4
and the table-driven seeders read section-2 constants that the exec prefix
can supply:

  1. Every number_input with a constant key= and constant bounds that is
     seeded from a share param -- directly (`setdefault(key, get_shared_*(...))`),
     via a local (`x = get_shared_*(...)` then `st.session_state[key] = x`),
     or through SAI_SHARE_FIELDS / REPAYMENT_SHARE_FIELDS -- passes lo/hi
     equal to the widget's min_value/max_value.
  2. A widget whose bound is computed at render time (the SAI family size
     follows the parents radio) still gets lo AND hi; only the values cannot
     be compared.
  3. Each loan-grid column in REPAYMENT_LOAN_LIST_PARAMS carries the bounds of
     the matching NumberColumn, and the seeder clamps with them.
  4. MAX_SHARED_LOANS exists, is a small int, and cuts the comma list before
     any row is built. Measured 2026-08-30: 3,000 balances in one 18 KB link
     cost 15 seconds of server time.

Negative controls run on every invocation, each against a mutated copy of the
source: the age seed with no bounds, the age seed with the wrong bound, the
loan-list slice removed, and an SAI table row with the wrong bound. A guard
that cannot fail is worse than none.
"""
import ast
import sys

APP = "app.py"

GETTERS = {"get_shared_int", "get_shared_float"}
TABLE_SEEDS = {
    # constant name -> which seeder loops over it
    "SAI_SHARE_FIELDS": "seed_sai_from_share",
    "REPAYMENT_SHARE_FIELDS": "seed_repayment_from_share",
}


def _const(node, names):
    """A literal, or a module-level name bound to one; else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const(node.operand, names)
        return None if inner is None else -inner
    if isinstance(node, ast.Name):
        return names.get(node.id)
    return None


def _module_constants(tree) -> dict:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            value = _const(node.value, out)
            if value is not None:
                out[node.targets[0].id] = value
    return out


def _kw(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _func_name(call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def widget_bounds(tree, names) -> dict:
    """key -> (min, max) for every keyed number_input. A bound that is not a
    literal or a module constant is recorded as the string "dynamic"."""
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _func_name(node) != "number_input":
            continue
        key = _kw(node, "key")
        if not isinstance(key, ast.Constant):
            continue
        bounds = []
        for arg in ("min_value", "max_value"):
            raw = _kw(node, arg)
            if raw is None:
                bounds.append(None)
            else:
                value = _const(raw, names)
                bounds.append("dynamic" if value is None else value)
        out[key.value] = tuple(bounds)
    return out


def grid_bounds(tree, names) -> dict:
    """column -> (min, max) per data_editor, keyed by the column SET so the
    federal grid (two columns) and the private grid (four) cannot be mixed."""
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _func_name(node) != "data_editor":
            continue
        config = _kw(node, "column_config")
        if not isinstance(config, ast.Dict):
            continue
        cols = {}
        for k, v in zip(config.keys, config.values):
            if isinstance(k, ast.Constant) and isinstance(v, ast.Call) \
                    and _func_name(v) == "NumberColumn":
                cols[k.value] = tuple(_const(_kw(v, a), names)
                                      for a in ("min_value", "max_value"))
        if cols:
            out[frozenset(cols)] = cols
    return out


def _getter_bounds(call, names):
    """(lo, hi) passed to a get_shared_* call; None where the kwarg is absent."""
    lo, hi = _kw(call, "lo"), _kw(call, "hi")
    return (None if lo is None else _const(lo, names),
            None if hi is None else _const(hi, names))


def direct_seeds(tree) -> list:
    """(key, getter call) for every seed that reaches session_state directly."""
    seeds = []
    local_getters = {}
    for node in ast.walk(tree):
        # st.session_state.setdefault("key", get_shared_int(...))
        if isinstance(node, ast.Call) and _func_name(node) == "setdefault" \
                and len(node.args) == 2 and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[1], ast.Call) \
                and _func_name(node.args[1]) in GETTERS:
            seeds.append((node.args[0].value, node.args[1]))
        # x = get_shared_int(...)   ...   st.session_state["key"] = x
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            if isinstance(target, ast.Name) and isinstance(value, ast.Call) \
                    and _func_name(value) in GETTERS:
                local_getters[target.id] = value
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Subscript) \
                and isinstance(node.targets[0].slice, ast.Constant) \
                and isinstance(node.value, ast.Name) \
                and node.value.id in local_getters:
            seeds.append((node.targets[0].slice.value, local_getters[node.value.id]))
    return seeds


def table_rows(tree, name) -> list:
    """[(key, bounds-or-None)] for a share table whose rows are 4-tuples."""
    assign = next((n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", "") == name for t in n.targets)), None)
    if assign is None:
        return None
    rows = []
    for row in assign.value.elts:
        if len(row.elts) < 4:
            rows.append((row.elts[0].value, "missing"))
            continue
        b = row.elts[3]
        if isinstance(b, ast.Constant) and b.value is None:
            rows.append((row.elts[0].value, None))
        elif isinstance(b, ast.Tuple) and len(b.elts) == 2:
            rows.append((row.elts[0].value, b))
        else:
            rows.append((row.elts[0].value, "malformed"))
    return rows


def _seeder_passes_bounds(tree, func_name) -> bool:
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == func_name), None)
    if fn is None:
        return False
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _func_name(node) in GETTERS:
            lo, hi = _kw(node, "lo"), _kw(node, "hi")
            if lo is None or hi is None:
                # The checkbox rows seed a bool and pass no bounds; only a
                # getter whose result is stored as a number must clamp.
                parent_ok = False
                for outer in ast.walk(fn):
                    if isinstance(outer, ast.Compare) and node in ast.walk(outer):
                        parent_ok = True
                if not parent_ok:
                    return False
    return True


def failures_for(source: str) -> list:
    tree = ast.parse(source)
    names = _module_constants(tree)
    widgets = widget_bounds(tree, names)
    grids = grid_bounds(tree, names)
    failures = []

    def check(key, lo, hi, where):
        bounds = widgets.get(key)
        if bounds is None or bounds == (None, None):
            return  # not a bounded number_input; nothing to clamp to
        wmin, wmax = bounds
        if lo is None or hi is None:
            failures.append(f"  {where}: seed of {key!r} passes no lo=/hi=; the widget "
                            f"is bounded {bounds} and an out-of-range link value "
                            f"resets it to the minimum")
            return
        for label, got, want in (("lo", lo, wmin), ("hi", hi, wmax)):
            if want == "dynamic" or want is None:
                continue
            if got != want:
                failures.append(f"  {where}: seed of {key!r} clamps {label}={got} "
                                f"but the widget's bound is {want}")

    # 1. direct seeds
    for key, call in direct_seeds(tree):
        lo, hi = _getter_bounds(call, names)
        check(key, lo, hi, f"line {call.lineno}")

    # 2. the two scalar tables, and that their seeders pass what they carry
    for const_name, seeder in TABLE_SEEDS.items():
        rows = table_rows(tree, const_name)
        if rows is None:
            failures.append(f"  {const_name} is gone")
            continue
        for key, b in rows:
            if b in ("missing", "malformed"):
                failures.append(f"  {const_name}: row {key!r} has no (lo, hi) "
                                f"fourth element")
                continue
            if b is None:
                if key in widgets and widgets[key] != (None, None):
                    failures.append(f"  {const_name}: row {key!r} carries None but "
                                    f"its widget is bounded {widgets[key]}")
                continue
            lo, hi = (_const(e, names) for e in b.elts)
            check(key, lo, hi, const_name)
        if not _seeder_passes_bounds(tree, seeder):
            failures.append(f"  {seeder} reads {const_name} but does not pass "
                            f"lo=/hi= to every numeric getter")

    # 3. the loan grids
    lists = next((n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "REPAYMENT_LOAN_LIST_PARAMS"
                          for t in n.targets)), None)
    if lists is None:
        failures.append("  REPAYMENT_LOAN_LIST_PARAMS is gone")
    else:
        for entry in lists.value.elts:
            cols = {}
            for col in entry.elts[1].elts:
                if len(col.elts) < 4 or not isinstance(col.elts[3], ast.Tuple):
                    failures.append(f"  loan grid column {col.elts[0].value!r} has "
                                    f"no (lo, hi)")
                    continue
                cols[col.elts[0].value] = tuple(_const(e, names)
                                                for e in col.elts[3].elts)
            grid = grids.get(frozenset(cols))
            if grid is None:
                failures.append(f"  no data_editor has exactly the columns "
                                f"{sorted(cols)}; the grid and the link disagree")
                continue
            for column, (lo, hi) in cols.items():
                if (lo, hi) != grid[column]:
                    failures.append(f"  loan grid column {column!r} clamps to "
                                    f"{(lo, hi)} but the NumberColumn says "
                                    f"{grid[column]}")

    # 4. the cap
    cap = names.get("MAX_SHARED_LOANS")
    if not isinstance(cap, int) or not 1 <= cap <= 100:
        failures.append(f"  MAX_SHARED_LOANS must be a small int, got {cap!r}")
    seeder = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name == "seed_repayment_from_share"), None)
    sliced = seeder is not None and any(
        isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice)
        and isinstance(n.slice.upper, ast.Name)
        and n.slice.upper.id == "MAX_SHARED_LOANS"
        for n in ast.walk(seeder))
    if not sliced:
        failures.append("  seed_repayment_from_share does not cut the comma list "
                        "at [:MAX_SHARED_LOANS]; a link can carry any number of loans")
    clamps = seeder is not None and any(
        isinstance(n, ast.Call) and _func_name(n) == "_clamp_shared"
        for n in ast.walk(seeder))
    if not clamps:
        failures.append("  seed_repayment_from_share does not clamp grid cells "
                        "through _clamp_shared")
    return failures


def negative_controls(source: str) -> list:
    """Each mutation must FAIL. Returns the names of any that passed."""
    age = 'get_shared_int("age", 30, lo=18, hi=80)'
    assert age in source, "the age seed moved; update the control"
    mutations = {
        "age seed with no bounds": source.replace(age, 'get_shared_int("age", 30)'),
        "age seed with the wrong bound":
            source.replace(age, 'get_shared_int("age", 30, lo=18, hi=90)'),
        "loan list not cut at MAX_SHARED_LOANS":
            source.replace('.split(",")[:MAX_SHARED_LOANS]', '.split(",")'),
        "SAI row with the wrong bound":
            source.replace('("sai_parent_agi",     "saa",  int, (0, 1000000))',
                           '("sai_parent_agi",     "saa",  int, (0, 999))'),
    }
    passed = []
    for label, mutated in mutations.items():
        assert mutated != source, f"control {label!r} did not change the source"
        if not failures_for(mutated):
            passed.append(label)
    return passed


def main() -> int:
    source = open(APP).read()
    failures = failures_for(source)
    disarmed = negative_controls(source)
    for label in disarmed:
        failures.append(f"  NEGATIVE CONTROL PASSED: {label} -- the guard is disarmed")
    if failures:
        print("check_share_bounds: FAIL")
        print("\n".join(failures))
        return 1
    widgets = widget_bounds(ast.parse(source), _module_constants(ast.parse(source)))
    seeds = direct_seeds(ast.parse(source))
    print(f"check_share_bounds: OK ({len(seeds)} direct seeds, "
          f"{len(widgets)} keyed number inputs, "
          f"4 negative controls fail as they should)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

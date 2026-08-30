#!/usr/bin/env python3
"""Guard: the two live deployments are configured the same way.

    python3 check_deploy_parity.py        (exit 1 on a violation)

THE APP RUNS ON TWO HOSTS AND THEY READ DIFFERENT FILES.

  - Railway serves worthmydegree.com from the Dockerfile. `.dockerignore`
    excludes `.streamlit/`, so config.toml NEVER REACHES THE CONTAINER and
    every setting has to be passed as a CLI flag by start.sh.
  - Streamlit Community Cloud serves the legacy studentloanroi.streamlit.app.
    It ignores start.sh entirely and reads .streamlit/config.toml.

start.sh says so itself: "Keep the two in step or the deploys diverge
silently." Nothing enforced it until this file, and it had already drifted.

WHY THIS EXISTS, precisely. On 2026-08-21 worthmydegree.com went down with a
403. Part of the attempted fix added enableCORS/enableXsrfProtection to
config.toml, it was merged, the app was rebooted, and it changed NOTHING --
because the live host does not read that file. The change was then recorded as
"had no effect", which sent the diagnosis somewhere else entirely. A setting
edited in the wrong file looks exactly like a setting that did not work.

THE RULE IS ONE-DIRECTIONAL, and the direction matters:

  Every setting in config.toml MUST appear in start.sh with the same value.

The reverse is deliberately not required. start.sh legitimately carries flags
that have no business in config.toml -- the port and bind address come from the
host, `headless` is meaningless on Community Cloud, and gatherUsageStats is a
container concern. Those live in CONTAINER_ONLY below. Anything else appearing
in start.sh and not in config.toml is still reported, because it means the
legacy host is running without a setting the container has.

TWO SETTINGS ARE REQUIRED OUTRIGHT, not merely kept in step (REQUIRED below).
`client.showErrorDetails` must be set, to "type" or "none", because Streamlit's
default is "full": an uncaught exception renders its type, message and whole
traceback -- file paths included -- in the browser of whichever visitor hit
it. Both files were silent on it until 2026-08-30, which parity alone reads as
agreement. And the Dockerfile must switch to a non-root USER before its CMD,
because start.sh writes every credential into the working directory at boot
and the process handles untrusted input.

Five negative controls run on every invocation, each against a mutated copy of
the files: showErrorDetails deleted from config.toml, set to "full" in both,
the USER line removed, start.sh missing a flag config.toml has, and USER set
without the working directory chowned to it.

WHAT THIS CANNOT CHECK: whether either host is actually running the committed
code. Railway redeploys on a merge to main and Community Cloud on its own
schedule, so a green check here says the two files agree, not that the two
servers do.
"""
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / ".streamlit" / "config.toml"
START = ROOT / "start.sh"
DOCKERIGNORE = ROOT / ".dockerignore"
DOCKERFILE = ROOT / "Dockerfile"

# Flags that belong to the container and have no config.toml counterpart.
# Each is here because the OTHER host either sets it itself or cannot use it.
CONTAINER_ONLY = {
    "server.port": "the host assigns it via $PORT",
    "server.address": "bind address is a container concern",
    "server.headless": "Community Cloud is already headless",
    "browser.gatherUsageStats": "set once per platform, not per app",
}

# Settings that must be PRESENT, with a value from the allowed set. Absence is
# not neutral here: it selects a Streamlit default that is wrong for a public
# deployment, and parity alone cannot see a key both files leave out.
REQUIRED = {
    "client.showErrorDetails": ({"type", "none"},
                                "the default 'full' renders tracebacks to visitors"),
}


def parse_start_flags(text: str) -> dict:
    """The --section.key value pairs start.sh passes to `streamlit run`."""
    flags = {}
    # --server.enableStaticServing true   /   --client.toolbarMode minimal
    for key, value in re.findall(r'--([a-zA-Z]+\.[a-zA-Z]+)\s+"?([^"\s\\]+)"?', text):
        flags[key] = value.strip('"')
    return flags


def parse_config_settings(text: str) -> dict:
    """The section.key values in .streamlit/config.toml, flattened."""
    data = tomllib.loads(text)
    out = {}
    for section, body in data.items():
        if isinstance(body, dict):
            for key, value in body.items():
                out[f"{section}.{key}"] = value
    return out


def normalize(value) -> str:
    """One spelling for a value however the two files write it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().strip('"').lower()


def _uncommented(text: str) -> list:
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def find_problems(config_text: str, start_text: str,
                  dockerignore_text, dockerfile_text) -> list:
    """Every disagreement between the two hosts' config, plus the required
    settings. Pure over the four file texts so the negative controls can hand
    it mutated copies."""
    problems = []
    config = parse_config_settings(config_text)
    flags = parse_start_flags(start_text)

    # THE LOAD-BEARING DIRECTION. A setting here and not there is a setting the
    # legacy host honours and the LIVE host silently ignores.
    for key, value in sorted(config.items()):
        if key not in flags:
            problems.append(
                f"  {key} is set in config.toml and NOT passed by start.sh, so "
                f"Community Cloud honours it and the Railway container -- which "
                f"is what worthmydegree.com serves -- does not. Add "
                f"`--{key} {normalize(value)}` to start.sh. This is the exact "
                f"shape of the 2026-08-21 enableCORS mistake.")
        elif normalize(flags[key]) != normalize(value):
            problems.append(
                f"  {key} disagrees: config.toml says {normalize(value)!r}, "
                f"start.sh passes {normalize(flags[key])!r}. The two hosts are "
                f"running different apps.")

    for key in sorted(flags):
        if key in config or key in CONTAINER_ONLY:
            continue
        problems.append(
            f"  {key} is passed by start.sh and absent from config.toml, so the "
            f"legacy host runs without it. Either add it to config.toml or, if "
            f"it is genuinely container-only, list it in CONTAINER_ONLY with a "
            f"reason.")

    # Required in BOTH files, with an allowed value. Checked against each file
    # separately: parity would pass two files that agree on the wrong value,
    # or on leaving it out.
    for key, (allowed, why) in REQUIRED.items():
        for label, table in (("config.toml", config), ("start.sh", flags)):
            if key not in table:
                problems.append(
                    f"  {key} is not set in {label}; {why}. Set it to one of "
                    f"{sorted(allowed)} in both files.")
            elif normalize(table[key]) not in allowed:
                problems.append(
                    f"  {key} is {normalize(table[key])!r} in {label}; {why}. "
                    f"Allowed: {sorted(allowed)}.")

    # The premise the whole guard rests on. If .streamlit/ ever stops being
    # excluded, config.toml reaches the container and start.sh's flags become
    # the redundant half instead -- worth knowing before debugging either.
    # COMMENTS STRIPPED FIRST. Checking for the bare string matched a
    # commented-out "#.streamlit/" too, so the negative control that disables
    # the exclusion passed -- the guard read its own documentation as
    # compliance, the flaw this repo already records against check_share_
    # coverage's loan-amount search.
    if dockerignore_text is not None and ".streamlit/" not in _uncommented(dockerignore_text):
        problems.append(
            "  .dockerignore no longer excludes .streamlit/, so the container "
            "may now read config.toml directly. That is not necessarily wrong, "
            "but start.sh's flags and this guard both assume the opposite.")

    # The container must not run the app as root. The LAST USER before CMD is
    # what the process runs as; an earlier USER app followed by USER root
    # would pass a naive "is there a USER line" check.
    if dockerfile_text is not None:
        # Backslash continuations joined first, so a `RUN useradd ... \\`
        # followed by `&& chown ...` reads as the one instruction it is.
        lines = _uncommented(dockerfile_text.replace("\\\n", " "))
        cmd_index = next((i for i, ln in enumerate(lines)
                          if ln.upper().startswith(("CMD ", "ENTRYPOINT "))), len(lines))
        users_before_cmd = [ln.split(None, 1)[1].strip() for ln in lines[:cmd_index]
                            if ln.upper().startswith("USER ")]
        effective = users_before_cmd[-1] if users_before_cmd else "root"
        if effective in ("root", "0") or effective.startswith(("root:", "0:")):
            problems.append(
                f"  Dockerfile runs the app as {effective!r}: no non-root USER "
                f"before CMD. start.sh writes every credential into the working "
                f"directory at boot, and the process handles untrusted input.")
        else:
            # The user must OWN the working directory, not just the files copied
            # into it. WORKDIR creates the directory as root before the user
            # exists and COPY --chown does not touch it, so start.sh's
            # `mkdir .streamlit` fails and the container boots with no secrets.
            # That shipped in #172 and the Railway deploy log caught it.
            workdir = next((ln.split(None, 1)[1].strip() for ln in lines
                            if ln.upper().startswith("WORKDIR ")), None)
            user = effective.split(":")[0]
            chowned = any(ln.upper().startswith("RUN ") and "chown" in ln
                          and user in ln and (workdir or "") in ln for ln in lines)
            if workdir and not chowned:
                problems.append(
                    f"  Dockerfile switches to USER {user!r} but never chowns "
                    f"{workdir} to it; start.sh cannot create .streamlit there "
                    f"and the container starts without secrets.")
    return problems


def negative_controls(config_text, start_text, dockerignore_text, dockerfile_text) -> list:
    """Each mutation must FAIL. Returns the names of any that passed."""
    cfg_line = 'showErrorDetails = "type"'
    sh_flag = "--client.showErrorDetails type"
    assert cfg_line in config_text and sh_flag in start_text, \
        "showErrorDetails moved; update the controls"
    assert "USER app" in dockerfile_text, "the USER line moved; update the control"
    cases = {
        "showErrorDetails deleted from config.toml":
            (config_text.replace(cfg_line, ""), start_text, dockerignore_text, dockerfile_text),
        "showErrorDetails set to full in both":
            (config_text.replace(cfg_line, 'showErrorDetails = "full"'),
             start_text.replace(sh_flag, "--client.showErrorDetails full"),
             dockerignore_text, dockerfile_text),
        "USER line removed from the Dockerfile":
            (config_text, start_text, dockerignore_text,
             dockerfile_text.replace("USER app", "")),
        "start.sh missing a flag config.toml has":
            (config_text, start_text.replace(sh_flag + " \\", ""),
             dockerignore_text, dockerfile_text),
        "USER set but the working directory not chowned":
            (config_text, start_text, dockerignore_text,
             dockerfile_text.replace("&& chown app:app /app", "")),
    }
    passed = []
    for label, texts in cases.items():
        assert texts != (config_text, start_text, dockerignore_text, dockerfile_text), label
        if not find_problems(*texts):
            passed.append(label)
    return passed


def main() -> int:
    problems = []
    if not CONFIG.exists():
        problems.append("  .streamlit/config.toml is missing.")
    if not START.exists():
        problems.append("  start.sh is missing, so the container has no config"
                        " at all.")
    if not DOCKERFILE.exists():
        problems.append("  Dockerfile is missing; worthmydegree.com has nothing to build.")
    if problems:
        print("deploy parity: cannot check\n")
        print("\n".join(problems))
        return 1

    texts = (CONFIG.read_text(), START.read_text(),
             DOCKERIGNORE.read_text() if DOCKERIGNORE.exists() else None,
             DOCKERFILE.read_text())
    problems = find_problems(*texts)
    for label in negative_controls(*texts):
        problems.append(f"  NEGATIVE CONTROL PASSED: {label} -- the guard is disarmed")

    if problems:
        print(f"deploy parity: {len(problems)} problem(s)\n")
        for p in problems:
            print(p + "\n")
        print("  Two hosts, two config files, one app. Railway reads start.sh's\n"
              "  flags because .dockerignore keeps config.toml out of the image;\n"
              "  Community Cloud reads config.toml and never runs start.sh.")
        return 1

    config, flags = parse_config_settings(texts[0]), parse_start_flags(texts[1])
    shared = sorted(set(config) & set(flags))
    print(f"deploy parity OK: {len(shared)} shared setting(s) agree "
          f"({', '.join(shared)}), {len(REQUIRED)} required setting(s) present, "
          f"container runs as a non-root user, "
          f"{len(CONTAINER_ONLY)} container-only flag(s) exempt, "
          f"5 negative controls fail as they should")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Select the conformance corpus declared by a compiler capability profile."""
import json
import sys
from pathlib import Path

CONFORMANCE = Path(__file__).resolve().parent.parent
SUITES = {
    "base": CONFORMANCE,
    "components": CONFORMANCE / "v1",
}
ACTIONS = ("include_all", "include", "exclude")


class ProfileError(Exception):
    pass


def _read_rows(path):
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        columns = line.split("\t")
        if len(columns) != 4:
            raise ProfileError("line %d is not four tab-separated columns" % number)
        rows.append(tuple(column.strip() for column in columns))
    return rows


def _suite_cases(name):
    root = SUITES.get(name)
    if root is None:
        raise ProfileError("unknown suite: " + name)
    index = root / "CASES.tsv"
    if not index.is_file():
        raise ProfileError("missing suite index: " + str(index))
    cases = []
    for number, line in enumerate(index.read_text(encoding="utf-8").splitlines()[1:], 2):
        if not line.strip():
            continue
        case = line.split("\t", 1)[0]
        if not case:
            raise ProfileError("missing case at %s:%d" % (index, number))
        cases.append(case)
    return cases


def select(rows, profile):
    include_all = set()
    includes = set()
    excludes = set()
    for row_profile, suite, action, case in rows:
        if row_profile != profile:
            continue
        if suite not in SUITES:
            raise ProfileError("profile %s references unknown suite %s" % (profile, suite))
        if action not in ACTIONS:
            raise ProfileError("profile %s has invalid action %s" % (profile, action))
        known = _suite_cases(suite)
        if action == "include_all":
            if case != "-":
                raise ProfileError("include_all case must be -")
            include_all.add(suite)
        else:
            if case not in known:
                raise ProfileError("profile %s references missing case %s/%s" %
                                   (profile, suite, case))
            (includes if action == "include" else excludes).add((suite, case))
    overlap = includes & excludes
    if overlap:
        raise ProfileError("case is both included and excluded")
    selected = set(includes)
    for suite in include_all:
        selected.update((suite, case) for case in _suite_cases(suite))
    stale = excludes - selected
    if stale:
        raise ProfileError("profile excludes a case that was not selected")
    selected.difference_update(excludes)
    if not selected:
        raise ProfileError("profile selects no cases: " + profile)
    return sorted(selected)


def profile_from_capabilities(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    profile = data.get("conformance_profile")
    if not isinstance(profile, str) or not profile:
        raise ProfileError("capabilities has no non-empty conformance_profile")
    return profile


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    profile = None
    capabilities = None
    as_json = False
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--json":
            as_json = True
        elif item in ("--profile", "--capabilities"):
            index += 1
            if index >= len(args):
                raise ProfileError(item + " requires a value")
            if item == "--profile":
                profile = args[index]
            else:
                capabilities = Path(args[index])
        else:
            raise ProfileError("unknown option: " + item)
        index += 1
    if (profile is None) == (capabilities is None):
        raise ProfileError("provide exactly one of --profile or --capabilities")
    if capabilities is not None:
        profile = profile_from_capabilities(capabilities)
    selected = select(_read_rows(CONFORMANCE / "profiles.tsv"), profile)
    names = ["%s:%s" % item for item in selected]
    if as_json:
        sys.stdout.write(json.dumps({"profile": profile, "cases": names},
                                    ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        for name in names:
            sys.stdout.write(name + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, ProfileError) as error:
        sys.stderr.write("profile-select: %s\n" % error)
        sys.exit(3)

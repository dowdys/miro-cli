#!/usr/bin/env python3
"""precommit-soldoc-secret-guard.py -- block committing an unredacted solution doc.

Second net beneath the Compound Miner's write-time redaction gate: BLOCK any
commit that stages a ``docs/solutions/**/*.md`` still containing a plaintext
BLOCKING secret (Supabase ``service_role`` JWT, ``sk-proj-`` / OpenAI key, AWS,
Stripe, GitHub token, PEM private key, ...).

Scan strategy -- reuse the SAME authoritative blocking scan the write-time gate
uses, so the two nets agree exactly:

  1. PREFERRED: ``src.pipeline.doc_redaction_gate.scan_for_blocking_secrets``.
     (The miner calls the same function at write time.)
  2. FALLBACK (import unavailable -- e.g. this script adopted in another repo):
     the SAME two detectors that scan composes, unioned:
       - gitleaks (v8) run over the doc's content, AND
       - ``src.lib.secret_patterns.scan`` restricted to the HARD_BLOCK tier.
     If neither ai-infra module is importable, gitleaks alone is the guard.

Every finding carries a FINGERPRINT ONLY -- never a raw secret value.

FAIL CLOSED: for any solution doc we could not scan (scanner error / unavailable,
unreadable staged blob) we BLOCK rather than let an unverified doc through --
better to block than to leak. Zero staged solution docs => pass fast (exit 0).

HONEST DENOMINATOR (2026-08-26). ``--all`` prints the number of files it ACTUALLY
examined, and that number is the scan loop's own iteration count -- never a
second enumeration that could disagree with what was scanned. Before this, a run
over 0 docs and a run over 3,988 docs emitted the identical string ("all tracked
solution docs are clean"), so a guard aimed at a path holding no files was
indistinguishable from a guard that looked and found nothing; only a timing
side-channel (0.13s vs 1.41s) told them apart. A security control that reports
clean when it examined nothing is the fabricated-clean shape in its most
dangerous location.

Zero WARNS, it does not FAIL, and that is deliberate:
  * A red soldoc-secret-guard must mean "a secret was found". Failing on an empty
    scope makes the same red also mean "this repo has no solution docs", and a
    check whose red has two meanings stops being believed.
  * The CI workflow's path filters mean a zero-doc repo only ever fires this job
    on guard-maintenance commits, so a hard failure would paint every future
    maintenance PR red in precisely the repos that most need the guard maintained
    -- which trains people to bypass it.
  * A repo can legitimately have no solution docs yet.
Zero is still LOUD: a distinct message that never contains the word "clean", a
GitHub Actions ``::warning`` annotation surfaced on the run and the PR, and a
job-summary line. Where an operator has established that zero IS wrong for a
given repo, arm ``--require-docs`` (or ``SOLDOC_GUARD_REQUIRE_DOCS=1``) and a
zero-file scan becomes exit 2 there.

Usage:
  precommit-soldoc-secret-guard.py [FILE ...]  # scan given files' STAGED content
  precommit-soldoc-secret-guard.py             # scan all staged sol-docs (diff --cached)
  precommit-soldoc-secret-guard.py --all       # scan every tracked sol-doc on disk (CI)
  precommit-soldoc-secret-guard.py --all --require-docs  # ...and 0 files is an ERROR

Exit codes: 0 = clean or no solution docs staged; 1 = a residual secret (or an
unscannable solution doc) was found -> the commit is blocked; 2 = --require-docs
was armed and the scan examined ZERO files (an empty scope, NOT a secret).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

# A staged path is a "solution doc" iff it matches this (repo-root-relative).
SOLDOC_RE = re.compile(r"^docs/solutions/.*\.md$")

REMEDIATION = (
    "Remediation: the doc still holds a live secret shape. Re-run the solution-doc\n"
    "  redaction sweep to scrub it to a placeholder, then re-stage the file. Do NOT\n"
    "  hand-paste the raw value back. See docs/soldoc-secret-guard.md for the sweep\n"
    "  command and the three-layer design (write-time gate -> backfill -> this hook)."
)


# --------------------------------------------------------------------------- #
# ai-infra location discovery (so the preferred import resolves wherever run).
# --------------------------------------------------------------------------- #
def _ai_infra_roots() -> List[str]:
    roots: List[str] = []

    def _add(p: str | None) -> None:
        if p and os.path.isdir(p) and p not in roots:
            roots.append(p)

    _add(os.environ.get("AI_INFRA_ROOT"))
    _add("/home/goduk/ai-infra")
    # repo root, if this script lives in <repo>/scripts/
    _add(str(Path(__file__).resolve().parent.parent))
    # current git top-level
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if top.returncode == 0:
            _add(top.stdout.strip())
    except Exception:
        pass
    return roots


def _setup_ai_infra_path() -> None:
    for root in _ai_infra_roots():
        if root not in sys.path:
            sys.path.insert(0, root)


def _import_secret_patterns():
    """Return the secret_patterns module, or None if unavailable."""
    try:
        from src.lib import secret_patterns  # type: ignore
        return secret_patterns
    except Exception:
        return None


def _gitleaks_bin() -> str:
    return os.environ.get(
        "DOC_GATE_GITLEAKS_BIN", str(Path.home() / ".local" / "bin" / "gitleaks")
    )


def _gitleaks_timeout() -> float:
    try:
        return float(os.environ.get("DOC_GATE_GITLEAKS_TIMEOUT_SEC", "300"))
    except ValueError:
        return 300.0


# --------------------------------------------------------------------------- #
# Scanner resolution: prefer the authoritative gate, else a faithful fallback.
# --------------------------------------------------------------------------- #
ScanFn = Callable[[str], List[dict]]


def _load_scanner() -> Tuple[ScanFn, type, str]:
    """Return ``(scan_fn, Unavailable, label)``.

    ``scan_fn(content) -> list[dict]`` ([] == clean; findings carry fingerprints).
    ``Unavailable`` is the exception type raised when the scan could not run and
    the caller must fail closed.
    """
    _setup_ai_infra_path()
    try:
        from src.pipeline.doc_redaction_gate import (  # type: ignore
            scan_for_blocking_secrets,
            BlockingScanUnavailable,
        )
        return scan_for_blocking_secrets, BlockingScanUnavailable, "authoritative (doc_redaction_gate)"
    except Exception:
        return _build_fallback_scanner()


def _build_fallback_scanner() -> Tuple[ScanFn, type, str]:
    """Replicate ``scan_for_blocking_secrets``: gitleaks UNION secret_patterns
    HARD_BLOCK tier. Used when the authoritative module cannot be imported."""

    class BlockingScanUnavailable(RuntimeError):
        """The gitleaks half could not run; fail closed."""

    gitleaks_bin = os.environ.get(
        "DOC_GATE_GITLEAKS_BIN", str(Path.home() / ".local" / "bin" / "gitleaks")
    )
    try:
        from src.lib import secret_patterns as _sp  # type: ignore
    except Exception:
        _sp = None  # type: ignore

    def _fingerprint(val: str) -> str:
        if _sp is not None:
            try:
                return _sp.fingerprint(val)
            except Exception:
                pass
        return hashlib.sha256(val.encode("utf-8", "replace")).hexdigest()[:8]

    def _hard(content: str) -> List[dict]:
        if _sp is None:
            return []
        out: List[dict] = []
        for f in _sp.scan(content):
            if f.get("tier") != "hard":
                continue
            out.append({
                "rule": f.get("type", "secret_pattern"),
                "fingerprint": f.get("fingerprint", ""),
                "count": f.get("count", 1),
                "source": "secret_patterns",
            })
        return out

    def _gitleaks(content: str) -> List[dict]:
        if not os.path.exists(gitleaks_bin):
            raise BlockingScanUnavailable(f"gitleaks binary missing at {gitleaks_bin}")
        work = tempfile.mkdtemp(prefix="soldoc-gl-")
        try:
            scan_dir = Path(work) / "scan"
            scan_dir.mkdir()
            (scan_dir / "candidate.md").write_text(content, encoding="utf-8")
            report = Path(work) / "report.json"
            try:
                timeout = float(os.environ.get("DOC_GATE_GITLEAKS_TIMEOUT_SEC", "60"))
            except ValueError:
                timeout = 60.0
            try:
                proc = subprocess.run(
                    [
                        gitleaks_bin, "dir", str(scan_dir),
                        "--report-format", "json",
                        "--report-path", str(report),
                        "--exit-code", "1",
                        "--no-banner",
                        "--log-level", "error",
                    ],
                    capture_output=True, text=True, timeout=timeout,
                )
            except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
                raise BlockingScanUnavailable(
                    f"gitleaks invocation failed: {type(exc).__name__}"
                ) from exc
            if proc.returncode not in (0, 1) or not report.exists():
                raise BlockingScanUnavailable(
                    f"gitleaks returned rc={proc.returncode}, report_exists={report.exists()}"
                )
            try:
                raw = json.loads(report.read_text() or "[]")
            except (ValueError, OSError) as exc:
                raise BlockingScanUnavailable(
                    f"gitleaks report unreadable: {type(exc).__name__}"
                ) from exc
            out: List[dict] = []
            for item in raw or []:
                secret = item.get("Secret") or ""
                out.append({
                    "rule": item.get("RuleID", "gitleaks"),
                    "fingerprint": _fingerprint(secret) if secret else "",
                    "line": item.get("StartLine"),
                    "source": "gitleaks",
                })
            return out
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def scan_for_blocking_secrets(content: str) -> List[dict]:
        if not isinstance(content, str) or not content:
            return []
        hard = _hard(content)
        try:
            gl = _gitleaks(content)
        except BlockingScanUnavailable:
            # If the in-process tier already proved the doc unsafe, it is unsafe
            # regardless of gitleaks. Otherwise we cannot vouch -> fail closed.
            if hard:
                return hard
            raise
        seen = set()
        union: List[dict] = []
        for f in hard + gl:
            key = (f.get("rule"), f.get("fingerprint"))
            if key in seen:
                continue
            seen.add(key)
            union.append(f)
        return union

    label = "fallback (gitleaks" + ("+secret_patterns" if _sp is not None else " only") + ")"
    return scan_for_blocking_secrets, BlockingScanUnavailable, label


# --------------------------------------------------------------------------- #
# Candidate discovery + content sourcing.
# --------------------------------------------------------------------------- #
def _git(args: List[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _staged_soldocs() -> List[str]:
    out = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", "docs/solutions"])
    return [p for p in out.splitlines() if p and SOLDOC_RE.match(p)]


def _all_tracked_soldocs() -> List[str]:
    out = _git(["ls-files", "--", "docs/solutions"])
    return [p for p in out.splitlines() if p and SOLDOC_RE.match(p)]


def _read_staged(path: str) -> str:
    """Read the STAGED blob content (what would actually be committed)."""
    proc = subprocess.run(["git", "show", f":{path}"], capture_output=True, text=True)
    if proc.returncode != 0:
        # Not in the index (should not happen for ACM files); fall back to disk.
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return proc.stdout


def _read_disk(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Batched whole-tree scan for --all (CI). One gitleaks subprocess over the whole
# docs/solutions dir (scanning 461 files one-at-a-time would spawn 461 gitleaks
# processes ~= minutes); secret_patterns runs in-process per doc (sub-ms).
# --------------------------------------------------------------------------- #
def _norm_key(path: str, git_root: str) -> str:
    """Normalize a path (abs OR relative) to a git-root-relative, forward-slash key
    via ``realpath``, so a gitleaks ``File`` value and a ``git ls-files`` doc path
    that name the SAME file compare equal even across symlinks / ``./`` segments /
    an abs-vs-rel mismatch. Falls back to a plain slash-normalized form if realpath
    can't resolve (e.g. the file no longer exists)."""
    p = path if os.path.isabs(path) else os.path.join(git_root, path)
    try:
        rel = os.path.relpath(os.path.realpath(p), os.path.realpath(git_root))
    except (OSError, ValueError):
        rel = path
    return rel.replace(os.sep, "/")


def _attribute_findings(raw_items, git_root: str, docs: List[str], sp) -> Tuple[dict, list]:
    """Map parsed gitleaks JSON items onto the set of SCANNED doc paths.

    Returns ``(by_file, unmapped)`` where ``by_file`` is keyed by the ORIGINAL
    ``docs`` path (so callers look up unchanged) and ``unmapped`` collects any
    finding whose ``File`` could not be attributed to a scanned doc. BOTH sides are
    normalized via :func:`_norm_key` so attribution is robust; a finding that still
    fails to map is COUNTED here (never silently dropped) so the caller fails CLOSED.
    Fingerprints only -- the raw ``Secret`` is hashed then discarded."""
    norm_to_doc = {_norm_key(d, git_root): d for d in docs}
    by_file: dict = {}
    unmapped: list = []
    for item in raw_items or []:
        f = item.get("File") or ""
        key = _norm_key(f, git_root)
        secret = item.get("Secret") or ""
        fp = ""
        if secret and sp is not None:
            try:
                fp = sp.fingerprint(secret)
            except Exception:
                fp = ""
        finding = {
            "rule": item.get("RuleID", "gitleaks"),
            "fingerprint": fp,
            "line": item.get("StartLine"),
            "source": "gitleaks",
        }
        doc = norm_to_doc.get(key)
        if doc is None:
            unmapped.append({**finding, "detail": f"unattributed File={key or '?'}"})
            continue
        by_file.setdefault(doc, []).append(finding)
    return by_file, unmapped


def _gitleaks_dir_findings(scan_root: str, git_root: str, sp, docs: List[str]) -> Tuple[dict, bool, list]:
    """Return ``(by_file, ok, unmapped)`` from ONE ``gitleaks dir`` pass over
    ``scan_root``. ``ok`` is False if gitleaks could not run (caller fails closed).
    ``by_file`` is keyed by the scanned ``docs`` paths; ``unmapped`` holds any
    gitleaks finding that could not be attributed to a scanned doc. Fingerprints
    only -- the raw ``Secret`` is hashed then discarded."""
    binp = _gitleaks_bin()
    if not os.path.exists(binp):
        return {}, False, []
    report = tempfile.NamedTemporaryFile(prefix="soldoc-all-", suffix=".json", delete=False)
    report.close()
    try:
        try:
            proc = subprocess.run(
                [
                    binp, "dir", scan_root,
                    "--report-format", "json",
                    "--report-path", report.name,
                    "--exit-code", "1",
                    "--no-banner",
                    "--log-level", "error",
                ],
                capture_output=True, text=True, timeout=_gitleaks_timeout(),
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return {}, False, []
        if proc.returncode not in (0, 1) or not os.path.exists(report.name):
            return {}, False, []
        try:
            raw = json.loads(Path(report.name).read_text() or "[]")
        except (ValueError, OSError):
            return {}, False, []
        by_file, unmapped = _attribute_findings(raw, git_root, docs, sp)
        return by_file, True, unmapped
    finally:
        try:
            os.unlink(report.name)
        except OSError:
            pass


def _sp_hard_findings(sp, content: str) -> List[dict]:
    if sp is None:
        return []
    out: List[dict] = []
    for f in sp.scan(content):
        if f.get("tier") != "hard":
            continue
        out.append({
            "rule": f.get("type", "secret_pattern"),
            "fingerprint": f.get("fingerprint", ""),
            "count": f.get("count", 1),
            "source": "secret_patterns",
        })
    return out


def _run_all_mode() -> Tuple[dict, str, int]:
    """Scan every tracked docs/solutions/**/*.md on disk (CI). Returns
    ``(offenders, label, scanned)``. Batched gitleaks UNION per-doc secret_patterns
    HARD_BLOCK -- the SAME two detectors the authoritative scan composes.

    ``scanned`` is the DENOMINATOR the caller reports. It is incremented as the
    FIRST statement of the per-doc loop body on every path that touches docs, so
    it counts iterations of the loop that does the scanning. It is deliberately
    NOT ``len(glob(...))``, not a re-run of ``_all_tracked_soldocs()``, and not a
    ``len(docs)`` taken elsewhere: a counter that can drift from the thing it
    counts is a brand-new fabricated-clean surface, which would be an absurd way
    to fix a fabricated-clean bug."""
    _setup_ai_infra_path()
    sp = _import_secret_patterns()
    try:
        git_root = _git(["rev-parse", "--show-toplevel"]).strip()
    except Exception:
        git_root = os.getcwd()

    scanned = 0
    docs = _all_tracked_soldocs()
    if not docs:
        return {}, "no solution docs", scanned

    # Absolute scan root: gitleaks then emits absolute `File` paths and the pass no
    # longer depends on cwd == git_root (both sides are realpath-normalized anyway).
    scan_root = os.path.join(git_root, "docs", "solutions")
    gl_by_file, gl_ok, gl_unmapped = _gitleaks_dir_findings(scan_root, git_root, sp, docs)

    # FAIL CLOSED (F1): gitleaks is the AUTHORITATIVE out-of-process blocking scanner
    # on the --all path, exactly as it is on the per-file path. If it could not run
    # cleanly (missing binary, timeout, crash, unreadable report) we CANNOT vouch for
    # the tree -> BLOCK, regardless of whether the in-process secret_patterns layer
    # imported. secret_patterns is an ADDITIONAL net here, NEVER a regex-only
    # downgrade substitute that silently passes the build. Any per-doc in-process
    # hits are still surfaced below the block marker, but the block stands.
    if not gl_ok:
        detail = ("no gitleaks and no secret_patterns"
                  if sp is None else
                  "authoritative gitleaks scan could not run; the in-process regex "
                  "layer is NOT an accepted substitute")
        offenders = {}
        for p in docs:
            scanned += 1
            offenders[p] = [{"rule": "SCANNER_UNAVAILABLE", "fingerprint": "",
                             "source": "guard", "detail": detail}]
            if sp is None:
                continue
            # MOVED, not new: this line came from the dict-comprehension form
            # below. The path is ALREADY blocking, so a secondary-scanner error
            # must not mask the block it is only decorating.
            try:
                extra = _sp_hard_findings(sp, _read_disk(p))
            except Exception:  # noqa: BLE001 - see comment above
                extra = []
            if extra:
                offenders[p].extend(extra)
        return offenders, "UNAVAILABLE gitleaks (fail-closed)", scanned

    offenders: dict = {}
    for p in docs:
        scanned += 1
        findings: List[dict] = []
        try:
            findings.extend(_sp_hard_findings(sp, _read_disk(p)))
        except Exception as exc:
            offenders[p] = [{"rule": "UNREADABLE_DOC", "fingerprint": "",
                             "source": "guard", "detail": type(exc).__name__}]
            continue
        findings.extend(gl_by_file.get(p, []))
        # de-dup by (rule, fingerprint)
        seen = set(); uniq = []
        for f in findings:
            key = (f.get("rule"), f.get("fingerprint"))
            if key in seen:
                continue
            seen.add(key); uniq.append(f)
        if uniq:
            offenders[p] = uniq

    # FAIL CLOSED (F4): a gitleaks finding whose File could not be attributed to any
    # scanned doc must NOT be silently dropped -- count it and BLOCK. This catches a
    # future path-key drift (gitleaks changing its File format, an unexpected
    # symlink) instead of letting a real finding vanish.
    if gl_unmapped:
        sys.stderr.write(
            f"[soldoc-guard] WARNING: {len(gl_unmapped)} gitleaks finding(s) could "
            f"not be attributed to a scanned doc; failing closed.\n"
        )
        offenders.setdefault("<unattributed gitleaks finding>", []).extend(
            {"rule": u.get("rule", "gitleaks"), "fingerprint": u.get("fingerprint", ""),
             "source": "guard", "detail": u.get("detail", "unattributed")}
            for u in gl_unmapped
        )

    label = "all-mode (gitleaks" + ("+secret_patterns" if sp is not None else " only") + ")"
    return offenders, label, scanned


# --------------------------------------------------------------------------- #
# Reporting.
# --------------------------------------------------------------------------- #
def _fmt_finding(f: dict) -> str:
    rule = f.get("rule", "?")
    fp = f.get("fingerprint", "") or "-"
    src = f.get("source", "?")
    line = f.get("line")
    detail = f.get("detail")
    loc = f"  L{line}" if line else ""
    extra = f"  ({detail})" if detail else ""
    return f"      - {rule:<22} fp={fp:<10}{loc}  [{src}]{extra}"


def _print_block(offenders: dict, label: str) -> None:
    err = sys.stderr.write
    err("\n")
    err("=========================================================================\n")
    err(" soldoc-secret-guard: COMMIT BLOCKED\n")
    err("=========================================================================\n")
    err(f" scanner: {label}\n")
    err(" A staged solution doc still contains a plaintext secret. Fingerprints only\n")
    err(" (the raw value is NEVER shown):\n\n")
    for path in sorted(offenders):
        err(f"  {path}\n")
        for f in offenders[path]:
            err(_fmt_finding(f) + "\n")
    err("\n")
    err(REMEDIATION + "\n")
    err(" This hook is the last of three nets: miner write-time gate -> backfill\n")
    err(" sweep -> this pre-commit/CI guard.\n")
    err("=========================================================================\n\n")


# --------------------------------------------------------------------------- #
# The --all verdict: a count, always, and a DIFFERENT string when it is zero.
# --------------------------------------------------------------------------- #
ZERO_SCAN_MSG = (
    "soldoc-secret-guard: scanned 0 files -- NOTHING WAS EXAMINED. "
    "This repository has no tracked docs/solutions/**/*.md, so the guard is "
    "aimed at an empty path. This is NOT a verdict about content: either the "
    "watched path is wrong for this repo, or the guard does not belong here."
)
# NOTE: ZERO_SCAN_MSG must never contain the word "clean" -- a test asserts it,
# because the whole defect was a zero-file run reading as a clean bill of health.


def _emit_all_verdict(scanned: int) -> None:
    """Report the --all verdict WITH its denominator.

    The zero case and the non-zero case emit different strings on purpose -- the
    defect this closes is that they used to emit the same one. Nothing here
    re-derives ``scanned``; it is passed in from the scan loop that produced it.
    """
    if scanned == 0:
        # Loud, three ways: a distinct message that never says "clean", a GitHub
        # annotation on the run + PR, and a job-summary line. Exit stays 0 --
        # see the module docstring for why zero warns rather than fails.
        print(ZERO_SCAN_MSG)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::warning title=soldoc-secret-guard scanned 0 files::"
                  + ZERO_SCAN_MSG)
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                try:
                    with open(summary, "a", encoding="utf-8") as fh:
                        fh.write("### :warning: soldoc-secret-guard scanned **0** files\n\n"
                                 + ZERO_SCAN_MSG + "\n")
                except OSError:
                    pass  # a summary we cannot write must never mask the stdout line
        return
    print(f"soldoc-secret-guard: scanned {scanned} files -- all clean.")


# --------------------------------------------------------------------------- #
# Main.
# --------------------------------------------------------------------------- #
def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(add_help=True, description="Block committing an unredacted solution doc.")
    ap.add_argument("--all", action="store_true",
                    help="scan every tracked docs/solutions/**/*.md on disk (CI mode)")
    ap.add_argument("--require-docs", action="store_true",
                    default=os.environ.get("SOLDOC_GUARD_REQUIRE_DOCS") == "1",
                    help="with --all: exit 2 if the scan examined ZERO files. Arm this "
                         "in a repo where an empty docs/solutions/ is known to be wrong.")
    ap.add_argument("files", nargs="*", help="staged files to scan (pre-commit passes these)")
    args = ap.parse_args(argv)

    if args.all:
        # CI whole-tree mode: batched gitleaks + per-doc secret_patterns.
        offenders, label, scanned = _run_all_mode()
        if offenders:
            _print_block(offenders, label)
            return 1
        _emit_all_verdict(scanned)
        if scanned == 0 and args.require_docs:
            sys.stderr.write(
                "[soldoc-guard] --require-docs is armed and the scan examined 0 files.\n"
            )
            return 2
        return 0

    if args.files:
        paths = [p for p in args.files if SOLDOC_RE.match(p)]
    else:
        paths = _staged_soldocs()
    reader = _read_staged

    paths = sorted(set(paths))
    if not paths:
        return 0  # pass fast -- nothing relevant staged

    scan_fn, Unavailable, label = _load_scanner()

    offenders: dict = {}
    for p in paths:
        try:
            content = reader(p)
        except Exception as exc:  # unreadable -> fail closed
            offenders[p] = [{"rule": "UNREADABLE_DOC", "fingerprint": "",
                             "source": "guard", "detail": type(exc).__name__}]
            continue
        try:
            findings = scan_fn(content)
        except Unavailable as exc:  # scanner down on a sol-doc -> fail closed
            offenders[p] = [{"rule": "SCANNER_UNAVAILABLE", "fingerprint": "",
                             "source": "guard", "detail": str(exc)}]
            continue
        except Exception as exc:  # any other scanner error -> fail closed
            offenders[p] = [{"rule": "SCANNER_ERROR", "fingerprint": "",
                             "source": "guard", "detail": type(exc).__name__}]
            continue
        if findings:
            offenders[p] = findings

    if offenders:
        _print_block(offenders, label)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

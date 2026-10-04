"""Check the RTL source checkout against HeMAiA's Bingo Bender pin, offline."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional


def _entry(text: str, section: str, name: str) -> str:
    match = re.search(rf"^{section}:\s*\n(.*?)(?=^\S|\Z)", text, re.M | re.S)
    if match is None:
        raise ValueError(f"Bender manifest has no {section} section")
    entries = re.findall(rf"^  {name}:[^\n]*(?:\n(?!  \S|\S).*)*", match[1], re.M)
    if len(entries) != 1:
        raise ValueError(f"Bender {section} must contain exactly one {name} entry")
    return entries[0]


def _git(checkout: Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(checkout), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"Cannot verify Bingo Git source: {' '.join(args)}") from exc


def _bender_rtl_inputs(manifest: str) -> List[str]:
    # Let Bender evaluate its source groups, not a hand-written SV file glob.
    # Dependencies do not affect the package's own source selection. Removing
    # them in this isolated query prevents any checkout or remote resolution.
    manifest = re.sub(r"^dependencies:.*?(?=^[\w-]+:|\Z)", "", manifest, flags=re.M | re.S)
    with tempfile.TemporaryDirectory(prefix="bingo_pin_") as tmp:
        root = Path(tmp)
        root.chmod(0o755)
        (root / "Bender.yml").write_text(manifest)
        (root / "Bender.lock").write_text("packages: {}\n")
        args = ["--local", "sources", "-n", "-f", "-t", "rtl", "-t", "hemaia",
                "-t", "simulation_hemaia", "-t", "simulation_hemaia_interposer_model"]
        bender = shutil.which("bender")
        if bender:
            command = [bender, "--dir", str(root), *args]
            query_root = root
        elif shutil.which("podman"):
            # No image pull and no writable mount of a real source checkout.
            query_root = Path("/bender-manifest")
            command = ["podman", "run", "--pull=never", "--rm",
                       "-v", f"{root}:{query_root}:ro", "-w", str(query_root),
                       "ghcr.io/kuleuven-micas/hemaia:main", "/tools/bender", *args]
        else:
            raise ValueError("Bingo pin verification needs local Bender or the existing HeMAiA container")
        try:
            result = subprocess.run(command, check=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            groups = json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            raise ValueError("Cannot enumerate Bingo RTL sources with offline Bender") from exc
        files = set()
        for group in groups:
            if group["package"] != "bingo_hw_manager":
                raise ValueError("Unexpected package in Bingo RTL source query")
            for raw in group["files"]:
                path = Path(raw)
                try:
                    relative = path.relative_to(query_root)
                except ValueError as exc:
                    raise ValueError(f"Bingo source outside its checkout: {path}") from exc
                if ".." in relative.parts:
                    raise ValueError(f"Bingo source escapes its checkout: {relative}")
                if path.suffix.lower() in (".sv", ".v", ".svh", ".vh"):
                    files.add(relative.as_posix())
        if not files:
            raise ValueError("Bender selected no Bingo RTL source files")
        return sorted(files)


def check_bingo_bender_pin(repo: Path, bingo_repo: Optional[Path] = None) -> Dict:
    """Pass matching HEADs; warn on RTL-identical divergence or dirty sources."""
    repo = Path(repo).resolve()
    dependency = _entry((repo / "Bender.yml").read_text(), "dependencies", "bingo_hw_manager")
    pins = re.findall(r"""\brev\s*:\s*["']?([0-9a-fA-F]{7,40})(?=["'\s,}]|$)""", dependency)
    if len(pins) != 1:
        raise ValueError("Bender.yml Bingo rev must be one explicit Git commit")
    locked = _entry((repo / "Bender.lock").read_text(), "packages", "bingo_hw_manager")
    paths = re.findall(r"^      Path:\s*(.*?)\s*$", locked, re.M)
    if len(paths) == 1:
        path = paths[0].strip("\"'")
        checkout = (repo / path).resolve()
        if bingo_repo is not None and checkout != Path(bingo_repo).resolve():
            raise ValueError(f"Bender compiles Bingo from {checkout}, not --bingo-repo {bingo_repo}")
    elif not paths:
        # A sibling --bingo-repo is only an extra container mount for Git
        # dependencies. Inspect Bender's checkout, not that unrelated sibling.
        git_sources = re.findall(r"^      Git:\s*(.*?)\s*$", locked, re.M)
        revisions = re.findall(r"^    revision:\s*([0-9a-fA-F]{40})\s*$", locked, re.M)
        if len(git_sources) != 1 or len(revisions) != 1:
            raise ValueError("Unsupported Bingo Bender.lock source")
        candidates = sorted({candidate.resolve() for candidate in
                             (repo / ".bender/git/checkouts").glob("bingo_hw_manager-*")
                             if candidate.is_dir()})
        if len(candidates) != 1:
            raise ValueError("Cannot uniquely resolve Bender's local Bingo Git checkout")
        checkout = candidates[0]
    else:
        raise ValueError("Bender.lock must resolve exactly one Bingo source directory")
    if not checkout.is_dir():
        raise ValueError(f"Bender Bingo checkout does not exist: {checkout}")
    head = _git(checkout, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    pin = _git(checkout, "rev-parse", "--verify", f"{pins[0]}^{{commit}}").decode().strip()
    dirty = bool(_git(checkout, "status", "--porcelain", "--", ".").strip())
    record = dict(pin=pin, head=head, checkout=str(checkout), dirty=dirty,
                  status="HEAD_MATCH", rtl_files=[])
    if head != pin:
        current_manifest = (checkout / "Bender.yml").read_bytes()
        pinned_manifest = _git(checkout, "show", f"{pin}:./Bender.yml")
        files = _bender_rtl_inputs(current_manifest.decode())
        pinned_files = (files if current_manifest == pinned_manifest else
                        _bender_rtl_inputs(pinned_manifest.decode()))
        if files != pinned_files:
            raise ValueError(f"Bingo HEAD {head} differs from pin {pin}: Bender RTL source list differs")
        different = []
        for relative in files:
            path = checkout / relative
            if not path.is_file() or path.read_bytes() != _git(checkout, "show", f"{pin}:./{relative}"):
                different.append(relative)
        if different:
            raise ValueError(f"Bingo HEAD {head} differs from pin {pin}: RTL differs: {', '.join(different)}")
        record.update(status="RTL_IDENTICAL", rtl_files=files)
        print(f"WARNING: Bingo HEAD {head} differs from Bender pin {pin}; "
              f"all {len(files)} Bender RTL sources are byte-identical ({checkout})")
    else:
        print(f"Bingo Bender pin matches HEAD {head} ({checkout})")
    if dirty:
        print(f"WARNING: Bingo source checkout has uncommitted changes: {checkout}")
    return record

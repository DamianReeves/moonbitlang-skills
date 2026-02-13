#!/usr/bin/env python3
"""Run MoonBit native tests with AddressSanitizer.

Snapshots each specified package file (`moon.pkg` DSL or `moon.pkg.json`),
patches `link.native` with platform ASan compiler settings, runs `moon test`,
and restores originals in a finally block.

The script patches:
  - `cc-flags`: ASan flags for MoonBit-generated C code
  - `stub-cc-flags`: appends ASan flags to existing stub compiler flags
  - `cc-link-flags`: prepends `-fsanitize=address` to existing linker flags

Both `moon.pkg` (DSL format) and `moon.pkg.json` (JSON format) are supported.
All specified `--pkg` files are patched (both library and is-main packages).
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ASAN_COMPILE_FLAGS = "-g -fsanitize=address"
ASAN_LINK_FLAG = "-fsanitize=address"


def macos_flags() -> dict[str, str]:
    """Try system clang first, fall back to Homebrew LLVM."""
    # System clang on modern macOS (Xcode 15+) supports ASan
    system_cc = shutil.which("cc") or "/usr/bin/cc"
    result = subprocess.run(
        [system_cc, "-fsanitize=address", "-x", "c", "-", "-o", "/dev/null"],
        input="int main(){return 0;}",
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        return {"cc": system_cc, "cc-flags": ASAN_COMPILE_FLAGS}

    # Fall back to Homebrew LLVM
    brew = shutil.which("brew")
    if not brew:
        if Path("/opt/homebrew/bin/brew").exists():
            brew = "/opt/homebrew/bin/brew"
        else:
            raise Exception(
                "System clang does not support ASan and Homebrew is not installed"
            )
    llvm_opts = ["llvm", "llvm@18", "llvm@19", "llvm@15", "llvm@13"]
    for llvm in llvm_opts:
        try:
            llvm_prefix = subprocess.run(
                [brew, "--prefix", llvm], check=True, text=True, capture_output=True
            ).stdout.strip()
        except subprocess.CalledProcessError:
            continue
        clang_path = Path(llvm_prefix) / "bin" / "clang"
        if clang_path.exists():
            return {"cc": str(clang_path), "cc-flags": ASAN_COMPILE_FLAGS}
    raise Exception(
        "No Homebrew LLVM installation found (tried: " + ", ".join(llvm_opts) + ")"
    )


def linux_flags() -> dict[str, str]:
    return {"cc": "gcc", "cc-flags": ASAN_COMPILE_FLAGS}


def windows_flags() -> dict[str, str]:
    return {
        "cc": "cl",
        "cc-flags": "/DEBUG /fsanitize=address",
        "stub-cc": "cl",
        "stub-cc-flags": "/DEBUG /fsanitize=address",
    }


def get_flags() -> dict[str, str]:
    system = platform.system()
    if system == "Darwin":
        return macos_flags()
    elif system == "Linux":
        return linux_flags()
    elif system == "Windows":
        return windows_flags()
    raise Exception(f"Unsupported platform: {system}")


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def resolve_pkg_path(repo_root: Path, pkg_arg: str) -> Path:
    requested = Path(pkg_arg)
    requested = requested if requested.is_absolute() else (repo_root / requested)

    # Build candidate list: try both formats regardless of which was specified
    candidates = []
    if requested.name == "moon.pkg":
        candidates.append(requested.with_name("moon.pkg.json"))
        candidates.append(requested)
    elif requested.name == "moon.pkg.json":
        candidates.append(requested)
        candidates.append(requested.with_name("moon.pkg"))
    else:
        candidates.append(requested)

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    searched = ", ".join(str(p) for p in candidates)
    sys.exit(f"Package file not found for --pkg {pkg_arg}. Tried: {searched}")


# ---------------------------------------------------------------------------
# JSON format patching (moon.pkg.json)
# ---------------------------------------------------------------------------


def patch_link_native_json(
    moon_pkg: dict[str, Any], flags: dict[str, str], pkg_path: Path
) -> None:
    """Patch link.native in a parsed JSON dict with ASan flags."""
    link = moon_pkg.get("link")
    if link is None:
        link = {}
        moon_pkg["link"] = link
    elif not isinstance(link, dict):
        raise ValueError(f'Expected "link" object in {pkg_path}')

    native = link.get("native")
    if native is None:
        native = {}
    elif not isinstance(native, dict):
        raise ValueError(f'Expected "link.native" object in {pkg_path}')

    if "cc" in flags:
        native["cc"] = flags["cc"]
    if "cc-flags" in flags:
        native["cc-flags"] = flags["cc-flags"]

    existing_stub_flags = native.get("stub-cc-flags", "")
    if "stub-cc-flags" in flags:
        native["stub-cc-flags"] = flags["stub-cc-flags"]
    elif existing_stub_flags:
        native["stub-cc-flags"] = existing_stub_flags + " " + ASAN_COMPILE_FLAGS
    else:
        native["stub-cc-flags"] = ASAN_COMPILE_FLAGS

    if "stub-cc" in flags:
        native["stub-cc"] = flags["stub-cc"]

    existing_link_flags = native.get("cc-link-flags", "")
    if existing_link_flags:
        native["cc-link-flags"] = ASAN_LINK_FLAG + " " + existing_link_flags
    else:
        native["cc-link-flags"] = ASAN_LINK_FLAG

    link["native"] = native


def patch_json_file(pkg_path: Path, flags: dict[str, str]) -> str:
    """Patch a moon.pkg.json file. Returns the patched text."""
    text = pkg_path.read_text(encoding="utf-8")
    try:
        moon_pkg = json.loads(text)
    except json.JSONDecodeError as error:
        sys.exit(f"Failed to parse JSON in {pkg_path}: {error}")
    if not isinstance(moon_pkg, dict):
        sys.exit(f"Package file is not a JSON object: {pkg_path}")
    try:
        patch_link_native_json(moon_pkg, flags, pkg_path)
    except ValueError as error:
        sys.exit(str(error))
    return json.dumps(moon_pkg, indent=2) + "\n"


# ---------------------------------------------------------------------------
# DSL format patching (moon.pkg)
# ---------------------------------------------------------------------------


def _find_native_block(text: str) -> tuple[int, int] | None:
    """Find the start and end positions of the "native": { ... } block.

    Returns (start_of_content, end_of_closing_brace) or None.
    """
    m = re.search(r'"native"\s*:\s*\{', text)
    if m is None:
        return None
    start = m.end()
    depth = 1
    pos = start
    while pos < len(text) and depth > 0:
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
        pos += 1
    return (start, pos)


def _detect_native_indent(text: str, content_start: int, content_end: int) -> str:
    """Detect the indentation used for entries inside the native block."""
    m = re.search(r"\n(\s+)\"", text[content_start : content_end - 1])
    return m.group(1) if m else "      "


def _replace_or_insert_in_native(text: str, key: str, value: str) -> str:
    """Replace an existing key's value or insert a new key-value pair in the native block."""
    # Try to replace existing key
    pattern = re.compile(rf'("{re.escape(key)}"\s*:\s*)"([^"]*)"')
    if pattern.search(text):
        return pattern.sub(rf'\g<1>"{value}"', text)

    # Insert new entry before closing } of "native" block
    bounds = _find_native_block(text)
    if bounds is None:
        raise ValueError('No "native" block found in moon.pkg')
    content_start, block_end = bounds
    closing_brace = block_end - 1
    indent = _detect_native_indent(text, content_start, block_end)
    insertion = f'{indent}"{key}": "{value}",\n'
    return text[:closing_brace] + insertion + text[closing_brace:]


def patch_dsl_file(pkg_path: Path, flags: dict[str, str]) -> str:
    """Patch a moon.pkg DSL file using text manipulation. Returns the patched text."""
    text = pkg_path.read_text(encoding="utf-8")

    if _find_native_block(text) is None:
        sys.exit(
            f'No "native" block found in {pkg_path}. '
            "Cannot patch ASan flags without an existing link.native section."
        )

    # 1. cc: set value
    if "cc" in flags:
        text = _replace_or_insert_in_native(text, "cc", flags["cc"])

    # 2. cc-flags: set value
    if "cc-flags" in flags:
        text = _replace_or_insert_in_native(text, "cc-flags", flags["cc-flags"])

    # 3. stub-cc-flags: append ASan flags (or override on Windows)
    if "stub-cc-flags" in flags:
        text = _replace_or_insert_in_native(
            text, "stub-cc-flags", flags["stub-cc-flags"]
        )
    else:
        m = re.search(r'"stub-cc-flags"\s*:\s*"([^"]*)"', text)
        if m:
            existing = m.group(1)
            new_value = f"{existing} {ASAN_COMPILE_FLAGS}"
            text = _replace_or_insert_in_native(text, "stub-cc-flags", new_value)
        else:
            text = _replace_or_insert_in_native(
                text, "stub-cc-flags", ASAN_COMPILE_FLAGS
            )

    # 4. stub-cc: set if provided (Windows)
    if "stub-cc" in flags:
        text = _replace_or_insert_in_native(text, "stub-cc", flags["stub-cc"])

    # 5. cc-link-flags: prepend -fsanitize=address
    m = re.search(r'"cc-link-flags"\s*:\s*"([^"]*)"', text)
    if m:
        existing = m.group(1)
        new_value = f"{ASAN_LINK_FLAG} {existing}" if existing else ASAN_LINK_FLAG
        text = _replace_or_insert_in_native(text, "cc-link-flags", new_value)
    else:
        text = _replace_or_insert_in_native(text, "cc-link-flags", ASAN_LINK_FLAG)

    return text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def is_dsl_format(pkg_path: Path) -> bool:
    """Check if a package file uses moon.pkg DSL format (vs moon.pkg.json)."""
    return pkg_path.name == "moon.pkg"


def main():
    parser = argparse.ArgumentParser(
        description="Run MoonBit native tests with AddressSanitizer"
    )
    parser.add_argument(
        "--repo-root", required=True, type=Path, help="Project repository root"
    )
    parser.add_argument(
        "--pkg",
        action="append",
        default=[],
        metavar="PKG_FILE",
        help=(
            "Relative path to moon.pkg or moon.pkg.json (repeatable). "
            "Either format is auto-detected. "
            "Must include ALL packages with native-stub or cc-link-flags."
        ),
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    if not repo_root.is_dir():
        sys.exit(f"--repo-root is not a directory: {repo_root}")

    pkg_paths: list[Path] = []
    seen_pkg_paths: set[Path] = set()
    for pkg_arg in args.pkg:
        pkg_path = resolve_pkg_path(repo_root, pkg_arg)
        if pkg_path in seen_pkg_paths:
            continue
        seen_pkg_paths.add(pkg_path)
        pkg_paths.append(pkg_path)
        resolved_name = pkg_path.name
        requested_name = Path(pkg_arg).name
        if requested_name != resolved_name:
            print(
                f"Resolved --pkg {pkg_arg} -> {display_path(pkg_path, repo_root)}"
            )

    if not pkg_paths:
        sys.exit(
            "No --pkg arguments provided. Specify at least one moon.pkg or moon.pkg.json."
        )

    flags = get_flags()
    print(f"Platform: {platform.system()}")
    print(f"ASan compiler: {flags['cc']}")
    print(f"ASan compile flags: {flags['cc-flags']}")

    # Snapshot originals
    snapshots: dict[Path, str] = {}
    for pkg_path in pkg_paths:
        snapshots[pkg_path] = pkg_path.read_text(encoding="utf-8")

    # Build environment
    env = os.environ.copy()
    if platform.system() != "Windows":
        env["MOON_AR"] = "/usr/bin/ar"
        if platform.system() == "Darwin":
            env["ASAN_OPTIONS"] = "detect_leaks=0"
        else:
            env["ASAN_OPTIONS"] = "detect_leaks=1"
        lsan_suppressions = repo_root / ".lsan-suppressions"
        if lsan_suppressions.exists():
            env["LSAN_OPTIONS"] = f"suppressions={lsan_suppressions}"

    try:
        for pkg_path in pkg_paths:
            if is_dsl_format(pkg_path):
                patched = patch_dsl_file(pkg_path, flags)
            else:
                patched = patch_json_file(pkg_path, flags)
            pkg_path.write_text(patched, encoding="utf-8")
            fmt = "DSL" if is_dsl_format(pkg_path) else "JSON"
            print(f"Patched ({fmt}): {display_path(pkg_path, repo_root)}")

        result = subprocess.run(
            ["moon", "test", "--target", "native", "-v"],
            cwd=repo_root,
            env=env,
        )
        sys.exit(result.returncode)
    finally:
        for pkg_path, original in snapshots.items():
            pkg_path.write_text(original, encoding="utf-8")
            print(f"Restored: {display_path(pkg_path, repo_root)}")


if __name__ == "__main__":
    main()

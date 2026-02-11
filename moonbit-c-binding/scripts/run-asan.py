#!/usr/bin/env python3
"""Run MoonBit native tests with AddressSanitizer.

Snapshots each specified moon.pkg.json, patches link.native with the
appropriate compiler/flags for the current platform, runs `moon test`,
and restores the originals in a finally block.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def macos_flags():
    brew = shutil.which("brew")
    if not brew:
        if Path("/opt/homebrew/bin/brew").exists():
            brew = "/opt/homebrew/bin/brew"
        else:
            raise Exception("Homebrew is not installed or not in PATH")
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
            return {"cc": str(clang_path), "cc-flags": "-g -fsanitize=address"}
    raise Exception("No Homebrew LLVM installation found (tried: " + ", ".join(llvm_opts) + ")")


def linux_flags():
    return {"cc": "gcc", "cc-flags": "-g -fsanitize=address"}


def windows_flags():
    return {"cc": "cl", "cc-flags": "/DEBUG /fsanitize=address"}


def get_flags():
    system = platform.system()
    if system == "Darwin":
        return macos_flags()
    elif system == "Linux":
        return linux_flags()
    elif system == "Windows":
        return windows_flags()
    raise Exception(f"Unsupported platform: {system}")


def main():
    parser = argparse.ArgumentParser(description="Run MoonBit native tests with AddressSanitizer")
    parser.add_argument("--repo-root", required=True, type=Path, help="Project repository root")
    parser.add_argument("--pkg", action="append", default=[], metavar="PKG_JSON",
                        help="Relative path to moon.pkg.json (repeatable)")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    if not repo_root.is_dir():
        sys.exit(f"--repo-root is not a directory: {repo_root}")

    pkg_paths = [repo_root / p for p in args.pkg] if args.pkg else []

    flags = get_flags()
    print(f"Platform: {platform.system()}")
    print(f"link.native flags: {json.dumps(flags, indent=2)}")

    # Snapshot originals
    snapshots: dict[Path, str] = {}
    for pkg_path in pkg_paths:
        if not pkg_path.exists():
            sys.exit(f"Package file not found: {pkg_path}")
        snapshots[pkg_path] = pkg_path.read_text(encoding="utf-8")

    # Build environment
    env = os.environ.copy()
    if platform.system() != "Windows":
        env["MOON_CC"] = flags["cc"] + " -g -fsanitize=address"
        env["MOON_AR"] = "/usr/bin/ar"
        env["ASAN_OPTIONS"] = "detect_leaks=1"
        lsan_suppressions = repo_root / ".lsan-suppressions"
        if lsan_suppressions.exists():
            env["LSAN_OPTIONS"] = f"suppressions={lsan_suppressions}"

    try:
        # Patch each moon.pkg.json with link.native
        for pkg_path in pkg_paths:
            moon_pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            if "link" not in moon_pkg:
                moon_pkg["link"] = {}
            moon_pkg["link"]["native"] = flags
            pkg_path.write_text(json.dumps(moon_pkg, indent=2) + "\n", encoding="utf-8")
            print(f"Patched: {pkg_path.relative_to(repo_root)}")

        # Run tests
        result = subprocess.run(
            ["moon", "test", "--target", "native", "-v"],
            cwd=repo_root, env=env,
        )
        sys.exit(result.returncode)
    finally:
        # Restore all originals
        for pkg_path, original in snapshots.items():
            pkg_path.write_text(original, encoding="utf-8")
            print(f"Restored: {pkg_path.relative_to(repo_root)}")


if __name__ == "__main__":
    main()

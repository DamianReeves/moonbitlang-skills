# Including C Library Sources

How to include C library sources in MoonBit native builds.

## 1. Inclusion Strategies

All files listed in `"native-stub"` must be in the same directory as `moon.pkg`.
Choose the strategy that matches your library:

| Strategy | When to use | Example |
|---|---|---|
| **Flatten + native-stub** | Library has nested source tree | tree-sitter, libuv |
| **Header-only** | Library is a single `.h` with `#define IMPL` | stb_image, miniaudio |
| **System library linking** | Pre-built library installed on system | LLVM, OpenSSL |

**Header-only libraries:** `#define` the implementation macro and `#include` the header in your stub file. No flattening needed.

```c
#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"
#include <moonbit.h>
```

**System libraries:** Include only headers in the stub and supply linker flags in `moon.pkg`:

```moonbit
link(
  native(
    "cc-flags": "-I/path/to/include",
    "cc-link-flags": "-L/path/to/lib -lmylib",
  )
)
```

> **Portability warning:** `-I`/`-L`/`-l` flags are GCC/Clang conventions. MSVC's `cl.exe` does not accept them.

**Nested source trees** need flattening — see below.

## 2. Why Flattening Is Needed

The `moon` toolchain compiles `native-stub` files from the package directory only — it does not recurse into subdirectories. C libraries with nested source trees (e.g., `src/unix/async.c`, `lib/src/parser.c`) cannot be used directly. Their sources must be **flattened** into the same directory as `moon.pkg` before building.

## 3. The `#` Filename Convention

When flattening, replace `/` with `#` to preserve the original path in the filename:

| Original path | Flattened filename |
|---|---|
| `lib/src/lib.c` | `tree-sitter#lib#src#lib.c` |
| `src/unix/async.c` | `uv#src#unix#async.c` |
| `src/uv-common.c` | `uv#src#uv-common.c` |
| `include/uv.h` | `uv#include#uv.h` |

The `#` character has no special meaning to the `moon` toolchain — these are literal filenames on disk. The convention simply makes it easy to trace a flattened file back to its original location in the library source tree.

The prefix (e.g., `tree-sitter`, `uv`) is the library name, set once when creating the `Project` object.

## 4. The `prepare.py` Pattern

Real projects share a common `Project` class that handles mangling, include rewriting, and conditional compilation. Here is a walkthrough of each method.

### `mangle(source)` — Generate the Flattened Filename

Joins the prefix and all path components with `#`:

```python
def mangle(self, source: Path) -> Path:
    return Path("#".join([self.prefix, *source.parts]))
```

Example: with `prefix="uv"`, `mangle(Path("src/unix/async.c"))` produces `Path("uv#src#unix#async.c")`.

### `relocate(source, content)` — Rewrite `#include` Directives

C files use `#include "relative/path.h"` to reference headers by their original directory structure. After flattening, those relative paths are invalid. `relocate` rewrites every `#include "..."` directive to reference the mangled filename instead.

```python
def relocate(self, source: Path, content: str) -> str:
    include_directories = [(self.source / source).parent, *self.include]
    headers = []

    def relocate(header: Path) -> Path:
        for directory in include_directories:
            resolved = ((directory / header).resolve()).relative_to(
                self.source.resolve()
            )
            if not (self.source / resolved).exists():
                continue
            relocated = self.mangle(resolved)
            if not (self.target / relocated).exists():
                self.copy(resolved, relocate=False)
                headers.append(resolved)
            return relocated
        return header

    def replace(match: re.Match) -> str:
        indent = match.group("indent")
        header: str = match.group("header")
        relocated = relocate(Path(header))
        return f'#{indent}include "{relocated.as_posix()}"'

    content = re.sub(
        r'#(?P<indent>\s*)include "(?P<header>.*?)"', replace, content
    )

    for source in headers:
        self.copy(source)

    return content
```

Key behaviors:

- Searches each `include_directories` entry to resolve the header's real path
- Calls `self.copy()` recursively to flatten any newly discovered headers
- Only rewrites `#include "..."` (quoted includes), not `#include <...>` (system includes)

### `copy(source, ...)` — Read, Transform, and Write a File

Reads the original file, optionally prepends platform defines, relocates includes, wraps in `#if`/`#endif` guards, and writes the mangled file to the target directory.

```python
def copy(
    self,
    source: Path,
    relocate: bool = True,
    condition: Optional[str] = None,
):
    target = self.mangle(source)
    content = (self.source / source).read_text(encoding="utf-8")
    if relocate:
        content = self.relocate(source, content)
    if condition is not None:
        content = self.condition(condition, content)
    (self.target / target).write_text(content, encoding="utf-8")
    self.copied.add(Path(target))
```

Parameters:

- `relocate=True` — rewrite `#include` directives (disable for headers copied during relocation to avoid infinite recursion)
- `condition` — wrap the entire file content in `#if condition` / `#endif`

### `configure(project)` — List Which Files to Copy

A standalone function that calls `project.copy()` for each source file, with optional platform conditions:

```python
def configure(project: Project):
    # Platform-independent sources
    for source in ["src/fs-poll.c", "src/timer.c", "src/uv-common.c"]:
        project.copy(Path(source))

    # Windows-only sources
    for source in ["src/win/async.c", "src/win/core.c"]:
        project.copy(source=Path(source), condition="defined(_WIN32)")

    # Unix-only sources
    for source in ["src/unix/async.c", "src/unix/core.c"]:
        project.copy(source=Path(source), condition="!defined(_WIN32)")
```

## 5. Minimal Example

For a library with a single amalgamated source file (e.g., tree-sitter), the script is short:

```python
#!/usr/bin/env python3
from pathlib import Path
from prepare_common import Project  # or define Project inline

def configure(project: Project):
    project.copy(Path("lib") / "src" / "lib.c")

def main():
    source = Path("src") / "tree-sitter"      # git submodule checkout
    target = Path("src")                        # package directory
    include = [source / "lib" / "include", source / "lib" / "src"]
    project = Project(source, target, include=include, prefix="tree-sitter")
    configure(project)

if __name__ == "__main__":
    main()
```

This produces a single file `tree-sitter#lib#src#lib.c` in the `src/` package directory. The stub file then includes it:

```c
#include "tree-sitter#lib#src#lib.c"
#include <moonbit.h>

MOONBIT_FFI_EXPORT
void *moonbit_ts_parser_new(void) { /* ... */ }
```

## 6. Advanced: Platform-Conditional Sources

For cross-platform libraries like libuv, different source files are needed for each OS. The `condition` parameter wraps file contents in preprocessor guards, so all platform variants can coexist in a single `native-stub` list.

```python
# Platform defines prepended to every source file
defines = """#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0A00
#define _CRT_DECLARE_NONSTDC_NAMES 0
#else
#define _FILE_OFFSET_BITS 64
#define _LARGEFILE_SOURCE
#endif

#ifdef __APPLE__
#define _DARWIN_UNLIMITED_SELECT 1
#define _DARWIN_USE_64_BIT_INODE 1
#endif

#ifdef __linux__
#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200112
#endif
"""

def configure(project: Project):
    # Common sources — no condition
    for source in ["src/fs-poll.c", "src/timer.c", "src/uv-common.c"]:
        project.copy(Path(source))

    # Windows-only
    for source in ["src/win/async.c", "src/win/core.c", "src/win/tcp.c"]:
        project.copy(source=Path(source), condition="defined(_WIN32)")

    # Unix-only
    for source in ["src/unix/async.c", "src/unix/core.c", "src/unix/tcp.c"]:
        project.copy(source=Path(source), condition="!defined(_WIN32)")

    # macOS-only
    for source in ["src/unix/darwin.c", "src/unix/fsevents.c"]:
        project.copy(source=Path(source), condition="defined(__APPLE__)")

    # Linux-only
    for source in ["src/unix/linux.c", "src/unix/procfs-exepath.c"]:
        project.copy(source=Path(source), condition="defined(__linux__)")
```

The resulting file `uv#src#win#async.c` on disk looks like:

```c
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
/* ... platform defines ... */
#endif
/* ... original source with rewritten includes ... */
#if defined(_WIN32)
/* ... file content ... */
#endif
```

This way, `moon` compiles all `.c` files on every platform, but the preprocessor guards ensure only the relevant code is included.

## 7. Auto-Updating `moon.pkg`

When a library has many source files, manually maintaining the `native-stub` array is error-prone. The `update_moon_pkg_json()` pattern programmatically writes it after copying:

```python
import json

def update_moon_pkg_json(project: Project, path: Path):
    moon_pkg_json = json.loads(path.read_text(encoding="utf-8"))
    native_stubs = []
    for copied in project.copied:
        if copied.suffix == ".c":
            native_stubs.append(copied.as_posix())
    native_stubs.sort()
    # Append the hand-written stub file at the end
    moon_pkg_json["native-stub"] = [*native_stubs, "stub.c"]
    path.write_text(json.dumps(moon_pkg_json, indent=2) + "\n", encoding="utf8")
```

Call it after `configure()`:

```python
project = Project(source, target, include=include, prefix="uv")
configure(project)
update_moon_pkg_json(project, Path("src") / "moon.pkg")
```

The `project.copied` set tracks every file written by `copy()`, so the `native-stub` list is always in sync with the actual files on disk.

## 8. Alternative: Flat Libraries

Some C libraries (e.g., Lua) already have a flat source directory with no subdirectories. In that case, no mangling is needed — just copy `.c` and `.h` files directly:

```python
def copy_lua_sources(lua_src_dir: Path, project_src_dir: Path):
    project_src_dir.mkdir(parents=True, exist_ok=True)
    for file in lua_src_dir.iterdir():
        if file.suffix in (".c", ".h"):
            shutil.copy2(file, project_src_dir / file.name)
```

Since the filenames are already flat (`lapi.c`, `llex.c`, etc.), they can be listed directly in `native-stub` without any `#` mangling or include rewriting.

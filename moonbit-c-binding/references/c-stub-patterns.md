# C Stub Patterns for MoonBit FFI

## Anatomy of a C Stub File

A C stub file is a `.c` file listed in `native-stub` within `moon.pkg.json`. It
contains C-side implementations of functions declared as `extern "C"` in MoonBit
source.

### Minimal Structure

```c
#include <moonbit.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

// Internal helpers (not exported to MoonBit)
static inline int
my_internal_helper(int x) {
  return x + 1;
}

// Exported function (callable from MoonBit)
MOONBIT_FFI_EXPORT
int32_t
moonbit_mylib_do_something(int32_t input) {
  return my_internal_helper(input);
}
```

### Required Elements

| Element | Purpose |
|---|---|
| `#include <moonbit.h>` | Provides MoonBit runtime types, macros, and memory management APIs. Both `<moonbit.h>` and `"moonbit.h"` work. |
| `MOONBIT_FFI_EXPORT` | Macro that must precede every function exported to MoonBit. Without it, the function will not be visible to the MoonBit linker. |

### Include Order

1. Third-party library sources/headers, 2. `moonbit.h`, 3. Standard C headers.

### Naming Conventions

- **Exported functions**: Prefix with `moonbit_<libname>_` (e.g., `moonbit_ts_parser_new`).
- **Internal helpers**: Use `static` to keep them file-scoped.

## Key moonbit.h API

| API | Returns | Purpose |
|---|---|---|
| `moonbit_make_external_object(destructor, size)` | `void *` | GC-tracked native resource; GC calls `destructor` on collection. Returns pointer to `size` bytes of payload. |
| `moonbit_make_bytes(len, init)` | `void *` | GC-managed byte array (MoonBit `Bytes`) |
| `moonbit_make_int32_array(len, init)` | `void *` | GC-managed int32 array |
| `moonbit_make_string(len, init)` | `void *` | GC-managed UTF-16 string (`len` = number of code units) |
| `moonbit_incref(ptr)` | `void` | Increment ref count (prevent GC of C-held objects) |
| `moonbit_decref(ptr)` | `void` | Decrement ref count (always pair with `incref`) |
| `Moonbit_array_length(arr)` | `int` | Length of any GC-managed array or bytes |
| `Moonbit_object_header(ptr)->rc` | -- | Access ref count (debugging only) |
| `MOONBIT_FFI_EXPORT` | -- | Macro; required on all exported functions |

Refer to [ownership-and-memory.md](./ownership-and-memory.md) for ownership
semantics and usage of these functions.

## C Library Inclusion Strategies

All files listed in `"native-stub"` must be in the same directory as `moon.pkg`.
To include external C library sources, flatten or copy them into the package
directory (manually or via a script), then either list them in `"native-stub"`
or `#include` them inside your stub file.

### Strategy A: Direct Source Inclusion

Include the library's `.c` files directly. Works well for small-to-medium or amalgamated
libraries. Copy or flatten the sources into your package directory first.

```c
#include "tree-sitter#lib#src#lib.c"   // literal filename produced by a prepare script
#include <moonbit.h>
```

### Strategy B: Header-Only Libraries

```c
#define LIBNAME_IMPLEMENTATION          // if needed to emit implementation
#include "libname.h"                   // header must be in same directory
#include <moonbit.h>
```

### Strategy C: System Library Linking

For pre-built system libraries, include only headers in the stub and supply compiler/linker
flags in `moon.pkg.json` (see Section 4). This is the case where `cc-flags`/`cc-link-flags`
are genuinely needed.

```c
#include <llvm-c/Core.h>
#include <moonbit.h>
```

## moon.pkg Configuration Examples

### Basic: Single Stub, Embedded Library Source

```moonbit
options(
  "native-stub": ["tree-sitter.c"],
  targets: {
    "parser.mbt": ["native"],
    "tree.mbt": ["native"]
  },
)
```

- `"native-stub"` lists C stub files; library sources are pulled in via `#include` inside the stub.
- `targets` gates individual `.mbt` files to specific backends.
- No `cc` override: `moon` uses TCC for debug builds (faster iteration).

### Multiple Library Source Files in native-stub

When a library is not amalgamated, flatten its source files into the package
directory and list them:

```moonbit
options(
  "native-stub": [
    "llhttp#api.c",
    "llhttp#http.c",
    "llhttp#llhttp.c",
    "stub.c"
  ],
  targets: {
    "llhttp.mbt": ["native"],
    "llhttp_test.mbt": ["native"]
  },
)
```

> The `#`-named files (e.g., `llhttp#api.c`) are literal filenames on disk,
> produced by a prepare script that flattened `llhttp/api.c` into
> `llhttp#api.c`. The `moon` toolchain does not interpret `#` as a path
> separator. The final entry `stub.c` is your own C stub with the
> `MOONBIT_FFI_EXPORT` functions.

### System Library with Compiler and Linker Flags

This is the case where `cc-flags`/`cc-link-flags` are genuinely needed — binding a pre-built system library.

```moonbit
options(
  "native-stub": ["wrap.c"],
  targets: {
    "ffi.mbt": ["native"]
  },
)

link(
  native(
    cc: "clang",
    "cc-flags": "-I<path-to-include> -DSOME_DEFINE -DANOTHER_DEFINE -w",
    "cc-link-flags": "-L<path-to-lib> -lLLVM-18 -lpthread -ldl -lm -lstdc++",
  )
)
```

> **Portability warning:** The `-I`/`-L`/`-l` flags are GCC/Clang conventions.
> On Windows, MSVC's `cl.exe` does not accept them. No cross-platform flag
> mechanism exists yet, so setting `cc-flags` breaks Windows portability.

### Field Reference

| Field | Purpose |
|---|---|
| `"native-stub"` | C source files to compile. All files must be in the same directory as `moon.pkg`. |
| `link(native(cc: ...))` | C compiler override (optional). Omit to let `moon` use TCC for debug builds. |
| `link(native("cc-flags": ...))` | **Compilation** flags: `-I` (includes), `-D` (defines), `-w` (warnings). Only for system libraries. |
| `link(native("cc-link-flags": ...))` | **Linker** flags: `-L` (lib paths), `-l` (lib names), `-rpath`. Only for system libraries. |
| `link(native("stub-cc": ...))` | C compiler for stub files only (compiled separately from MoonBit-generated C) |
| `link(native("stub-cc-flags": ...))` | **Compilation** flags for stub files only |
| `link(native("stub-cc-link-flags": ...))` | **Linker** flags for stub files only |
| `targets` | Per-file backend gating, e.g. `"foo.mbt": ["native"]` |

> **Warning — `supported-targets`:** Avoid using `supported-targets: ["native"]`
> unless absolutely necessary. It prevents downstream packages from building on
> other targets. Use `targets` to gate individual files instead.

## Quick Reference Checklist

When writing a new C stub:

1. Place all C source files in the same directory as `moon.pkg`. Flatten
   external library sources if needed.
2. Include `<moonbit.h>` (or `"moonbit.h"`).
3. Mark every exported function with `MOONBIT_FFI_EXPORT`.
4. Prefix exported function names with `moonbit_<libname>_`.
5. Use `moonbit_make_external_object` for any native resource that needs cleanup.
6. Use `static inline` for internal helpers.
7. List all C source files in `"native-stub"` in `moon.pkg`.
8. Only set `cc-flags`/`cc-link-flags` when binding a system library. Omit them
   otherwise to preserve TCC debug builds and Windows portability.
9. Gate native-only `.mbt` files in `targets`. Avoid `supported-targets` unless
   absolutely necessary — it prevents downstream packages from building on other
   targets.

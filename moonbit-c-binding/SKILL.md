---
name: moonbit-c-binding
description: Guide for writing MoonBit bindings to C libraries using native FFI. Use when adding extern "c" declarations, writing C stubs with moonbit.h, configuring native-stub and link.native in moon.pkg or moon.pkg.json, choosing #borrow/#owned ownership annotations, designing callback trampolines, wrapping C pointers with external objects and finalizers, converting strings across FFI, or validating bindings with AddressSanitizer.
---

# MoonBit C Binding Guide

Step-by-step workflow for binding any C library to MoonBit using native FFI.

## When To Use

Use this skill when:

- Adding `extern "c" fn` declarations for a C library
- Writing C stub files (`moonbit.h`, `MOONBIT_FFI_EXPORT`)
- Configuring `moon.pkg` or `moon.pkg.json` for native builds (`native-stub`, `link.native`)
- Choosing `#borrow` vs ownership transfer for FFI parameters
- Wrapping C handles with external objects and finalizers
- Implementing callback trampolines (closures or `FuncRef`)
- Converting strings between MoonBit (UTF-16) and C (UTF-8)
- Running AddressSanitizer to catch memory bugs in bindings

## Plan Mode Instructions

**If you are in plan mode** and creating a plan for moonbit-c-binding work:

1. In your final plan file, include an explicit instruction at the beginning that tells the executing agent to read the moonbit-c-binding skill before starting implementation
2. The instruction should look like this:
   ```
   **Before starting implementation:** Use the Skill tool to load the moonbit-c-binding skill, which provides comprehensive guidance on FFI declarations, ownership annotations, C stubs, and AddressSanitizer validation.
   ```
3. This ensures the executing agent has access to all the critical patterns and workflows documented in this skill

## Workflow

Follow these 6 phases in order. Do not start coding before completing Phase 2.

### Phase 1: Project Setup

Set up `moon.mod.json` and `moon.pkg` for native compilation.

**Module configuration (`moon.mod.json`):** Add `"preferred-target": "native"` so that `moon build`, `moon test`, and `moon run` default to the native backend without requiring `--target native` every time:

```json
{
  "preferred-target": "native"
}
```

**Package configuration (`moon.pkg`):**

```moonbit
options(
  "native-stub": ["stub.c"],
  targets: {
    "ffi.mbt": ["native"]
  },
)
```

**Key fields:**

| Field | Purpose |
|---|---|
| `"native-stub"` | C source files to compile. All files must be in the same directory as `moon.pkg`. |
| `targets` | Gate individual `.mbt` files to specific backends: `"ffi.mbt": ["native"]` |
| `link(native(cc: ...))` | C compiler override (optional). Omit to let `moon` use TCC for debug builds (much faster). |
| `link(native(cc-flags: ...))` | Compile flags: `-I`, `-D`, `-w`. Only needed when binding a system library. |
| `link(native(cc-link-flags: ...))` | Linker flags: `-L`, `-l`, `-rpath`. Only needed when binding a system library. |
| `link(native(stub-cc: ...))` | C compiler for stub files only (separate from MoonBit-generated C) |
| `link(native(stub-cc-flags: ...))` | Compile flags for stub files only |
| `link(native(stub-cc-link-flags: ...))` | Linker flags for stub files only |

> **Warning — `supported-targets`:** Avoid using `supported-targets: ["native"]` unless absolutely necessary. It prevents downstream packages from building on other targets. For example, a `fetch` package depending on both `fetch_native` (native-only) and `fetch_js` (js-only) cannot build on either backend because each violates the other's constraint. Use `targets` to gate individual files instead.

> **Warning — `cc`/`cc-flags` portability:** Setting `cc` disables TCC for debug builds, making iteration slower. Setting `cc-flags` with `-I`/`-L`/`-l` flags breaks Windows portability since MSVC's `cl.exe` does not accept these flags. Only set these when binding a system library that genuinely needs extra flags.

**Including library sources:** All files listed in `"native-stub"` must be in the same directory as `moon.pkg`. To include external C library sources, flatten or copy them into the package directory (manually or via a `pre-build` script), then either list them in `"native-stub"` or `#include` them inside your stub file. For details on flattening C library sources and the `#` filename convention, see @references/amalgamation-build.md.

For system libraries, include only headers and supply `-l` flags via `cc-link-flags`.

### Phase 2: Type Mapping

Map C types to MoonBit types before writing any declarations.

| C Type | MoonBit Type | Notes |
|---|---|---|
| `int`, `int32_t` | `Int` | 32-bit signed |
| `uint32_t` | `UInt` | 32-bit unsigned |
| `int64_t` | `Int64` | 64-bit signed |
| `uint64_t` | `UInt64` | 64-bit unsigned |
| `float` | `Float` | 32-bit float |
| `double` | `Double` | 64-bit float |
| `bool` | `Bool` | |
| `uint8_t`, `char` | `Byte` | Single byte |
| `void` | `Unit` | Return type only |
| `void *` (opaque handle) | `type Handle` (opaque) | External object with finalizer |
| `const uint8_t *`, `uint8_t *` | `Bytes` or `FixedArray[Byte]` | Use `#borrow` if C doesn't store it |
| `const char *` (UTF-8 string) | `Bytes` | MoonBit `Bytes` is null-terminated; can be passed directly to C. Convert with `@encoding/utf8` in MoonBit |
| `struct *` (small, no cleanup) | `struct Foo(Bytes)` | Value-as-Bytes pattern |
| `struct *` (needs cleanup) | `type Foo` (opaque) | External object with finalizer |
| `int` (enum/flags) | `UInt` or `Int` | Map to MoonBit enum in wrapper |
| callback function pointer | closure `(A, B) -> C` or `FuncRef[(A, B) -> C]` | See Phase 5 |
| output `int *` | `Ref[Int]` | Borrow the Ref |

### Phase 3: Extern Declarations

Write `extern "c" fn` declarations in a dedicated `.mbt` file gated to the native backend in `targets`. Keep them private; expose safe wrappers as the public API.

```mbt nocheck
// Private extern declarations
///|
#borrow(parser)
extern "c" fn ts_parser_language(parser : Parser) -> Language = "moonbit_ts_parser_language"

///|
#borrow(parser, old_tree, string)
extern "c" fn ts_parser_parse_bytes(
  parser : Parser,
  old_tree : TSTree,
  string : Bytes,
) -> TSTree = "moonbit_ts_parser_parse_string"

// Public wrapper with safe API
///|
pub fn Parser::parse(self : Parser, source : Bytes) -> Tree {
  let raw = ts_parser_parse_bytes(self, TSTree::null(), source)
  // null check, error handling, etc.
  Tree::new(raw)
}
```

**Ownership annotations:**

| Annotation | When to use |
|---|---|
| `#borrow(param)` | C only reads during the call, does not store a reference |
| `#owned(param)` | Ownership transfers to C; C must `moonbit_decref` when done |

**Rules:**
- Annotate every non-primitive parameter as `#borrow` or `#owned`.
- Primitives (`Int`, `UInt`, `Bool`, `Double`, etc.) are passed by value -- no annotation needed.
- If unsure whether C stores a reference, do NOT use `#borrow`. Investigate the C API first.
- Use `Ref[T]` with `#borrow` for output parameters where C writes a value back.

### Phase 4: C Stub Implementation

Write a C stub file with `moonbit.h` and `MOONBIT_FFI_EXPORT`.

**Template:**

```c
#include <moonbit.h>
#include <stdint.h>
#include <string.h>

// --- Internal helpers ---

static void
moonbit_mylib_handle_delete(void *ptr) {
  MyHandle *h = (MyHandle *)ptr;
  mylib_free(h->handle);  // Release C resource only, not container
}

// --- Exported functions ---

MOONBIT_FFI_EXPORT
void *
moonbit_mylib_handle_new(int32_t config) {
  typedef struct { void *handle; } MoonBitMyHandle;
  MoonBitMyHandle *wrapper = (MoonBitMyHandle *)moonbit_make_external_object(
    moonbit_mylib_handle_delete, sizeof(void *)
  );
  wrapper->handle = mylib_create(config);
  return wrapper;
}

MOONBIT_FFI_EXPORT
int32_t
moonbit_mylib_process(void *handle, moonbit_bytes_t data) {
  // #borrow parameters: C does not own them, no decref needed
  size_t len = Moonbit_array_length(data);
  return mylib_process(((MoonBitMyHandle *)handle)->handle, data, len);
}
```

**Naming conventions:**
- Exported: `moonbit_<libname>_<operation>`

**External object pattern** (for C handles needing cleanup):

```c
typedef struct {
  TSParser *parser;
} MoonBitTSParser;

static void moonbit_ts_parser_delete(void *ptr) {
  MoonBitTSParser *p = (MoonBitTSParser *)ptr;
  ts_parser_delete(p->parser);
  // Do NOT free ptr -- GC manages the container
}

MOONBIT_FFI_EXPORT
MoonBitTSParser *moonbit_ts_parser_new(void) {
  MoonBitTSParser *parser = (MoonBitTSParser *)moonbit_make_external_object(
    moonbit_ts_parser_delete, sizeof(TSParser *)
  );
  parser->parser = ts_parser_new();
  return parser;
}
```

**Value-as-Bytes pattern** (for small structs without cleanup):

```c
MOONBIT_FFI_EXPORT
void *moonbit_mylib_settings_new(void) {
  return moonbit_make_bytes(sizeof(mylib_settings_t), 0);
}
```

### Phase 5: High-Level MoonBit API

Build safe wrappers over the raw externs.

**Opaque types:**

```mbt nocheck
///|
type Parser          // backed by external object (has finalizer)

///|
struct Settings(Bytes)  // backed by GC-managed bytes (no finalizer)

///|
struct Node(Bytes)      // small value struct as bytes
```

**Safe constructors and methods:**

```mbt nocheck
///|
pub fn Parser::new() -> Parser {
  ts_parser_new()
}

///|
pub fn Parser::set_language(self : Parser, language : Language) -> Bool {
  ts_parser_set_language(self, language)
}
```

**String conversion** -- prefer passing Bytes (UTF-8) across FFI:

```mbt nocheck
///|
pub fn Node::string(self : Node, tree : Tree) -> String {
  let bytes = ts_node_string(self.node, tree.tree)
  @encoding/utf8.decode_lossy(bytes)
}
```

MoonBit `Bytes` is always null-terminated by the runtime. When you write `let a : Bytes = [1, 2, 3, 4]`, the runtime allocates extra space for a trailing `\0`, so `Bytes` can be passed directly to C functions expecting `const char *` without manual null-termination.

**Callbacks:**

*FuncRef + Callback trick* -- for C functions that accept both a function pointer and callback data (recommended):

```mbt nocheck
///|
extern "c" fn register_callback_ffi(
  call_closure : FuncRef[(() -> Unit) -> Unit],
  closure : () -> Unit
) = "register_callback"

///|
pub fn register_callback(callback : () -> Unit) -> Unit {
  register_callback_ffi(fn(f) { f() }, callback)
}
```

On the C side, the library function accepts `void (*callback)(void*)` and `void *data`. MoonBit passes a closed function as the callback and the actual closure as data. The C function invokes the closed function with the data, effectively performing partial application. This works with any C API that supports callback data.

*Closure-as-callback* -- when the C API does not support callback data:

```mbt nocheck
///|
struct Logger((UInt, Bytes) -> Unit)

///|
fn Logger::new(log : (LogType, StringView) -> Unit) -> Logger {
  fn(log_type, message) {
    log(LogType::of_uint(log_type), @encoding/utf8.decode_lossy(message))
  }
}

///|
#borrow(parser)
extern "c" fn ts_parser_set_logger(parser : Parser, logger : Logger) = "moonbit_ts_parser_set_logger"
```

The C side requires a trampoline struct whose first field is the function pointer, with `self` as its first parameter. Only use this when the C API does not support callback data.

*FuncRef* -- when the callback is a plain function reference (no captures):

```mbt nocheck
///|
pub extern "c" fn Settings::on_message_begin(
  self : Settings,
  callback : FuncRef[(Parser) -> Errno],
) -> Unit = "moonbit_llhttp_settings_set_on_message_begin"
```

**Error mapping:**

```mbt nocheck
///|
pub fn result_from_status(status : Int) -> Unit raise {
  if status < 0 {
    raise MyLibError(status)
  }
}
```

### Phase 6: Testing and Validation

**Run tests:**

```bash
moon test --target native -v
```

**Run with AddressSanitizer** (catches use-after-free, leaks, overflows):

```bash
python3 scripts/run-asan.py \
  --repo-root <project-root> \
  --pkg moon.pkg \
  --pkg main/moon.pkg
```

The script supports both `moon.pkg` (DSL format) and `moon.pkg.json` (JSON
format). It auto-detects the format, sets `MOON_CC`/`MOON_AR` env vars to
override the compiler, and patches ASan flags into `cc-flags`, `stub-cc-flags`,
and `cc-link-flags`. On macOS it prefers Homebrew LLVM (enables leak detection
via LSan), falling back to system clang (ASan only). Pass `--pkg` for ALL
packages with `native-stub` or `cc-link-flags`.

See the ASan validation reference for platform setup and troubleshooting.

## Decision Table

| Situation | Pattern | Key Action |
|---|---|---|
| C reads pointer only during call | `#borrow(param)` | No decref in C |
| C takes ownership of pointer | `#owned(param)` | C must `moonbit_decref` |
| C handle needs cleanup on GC | External object + finalizer | `moonbit_make_external_object` |
| Small C struct, no cleanup | Value-as-Bytes | `moonbit_make_bytes` + `struct Foo(Bytes)` |
| C returns null on failure | Nullable wrapper | Check null, return `Option` or raise error |
| Callback with data parameter | FuncRef + Callback trick | Pass closed fn as callback, closure as data |
| Callback without data parameter | Closure-as-callback | Trampoline struct, first field = fn ptr |
| Stateless callback | `FuncRef[(A) -> B]` | Direct function pointer |
| C string (UTF-8) output | Pass `Bytes` across FFI | Decode with `@encoding/utf8` in MoonBit |
| Output parameter (`int *result`) | `Ref[T]` with `#borrow` | C writes into Ref, MoonBit reads `.val` |

## Common Pitfalls

1. **Using `#borrow` when C stores the pointer.** The GC may collect the object while C holds a stale reference. Only borrow for call-scoped access.

2. **Forgetting `moonbit_decref` on owned parameters.** Every non-borrowed, non-primitive parameter transfers ownership to C. Missing decrefs leak memory.

3. **Calling `free()` on external object containers.** The GC manages the container. Finalizers must only release the inner C resource.

4. **Using `moonbit_make_bytes` for structs with inner pointers.** Bytes have no finalizer, so inner heap allocations leak. Use external objects instead.

5. **Missing `moonbit_incref` before callback invocation.** When C calls back into MoonBit, the GC may run. Incref MoonBit-managed objects before the call; decref afterward.

6. **Forgetting the `MOONBIT_FFI_EXPORT` macro.** Without it, the function is invisible to the MoonBit linker.

7. **Placing native-stub C files in subdirectories.** All files listed in `"native-stub"` must be in the same directory as `moon.pkg`.

8. **Not gating native-only files in `targets`.** Files with native-only externs must be listed in `targets` to avoid errors on other backends.

9. **Using `supported-targets` unnecessarily.** This prevents downstream packages from building on other targets. Use `targets` to gate individual files instead.

10. **Setting `cc`/`cc-flags` when not needed.** This disables TCC debug builds (slower iteration) and breaks Windows portability.

## References

Consult these for detailed patterns and examples:

@references/c-stub-patterns.md
@references/ownership-and-memory.md
@references/callbacks-and-external-objects.md
@references/asan-validation.md
@references/amalgamation-build.md

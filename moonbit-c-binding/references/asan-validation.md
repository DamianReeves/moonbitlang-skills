# AddressSanitizer (ASan) Validation for MoonBit C Bindings

Reference for detecting memory bugs in C stub code using AddressSanitizer.

## Why ASan Matters

C bindings introduce manual memory management invisible to MoonBit's GC. These bugs
silently corrupt memory or leak resources. ASan catches them at runtime:

| Bug Class | Typical Cause in Bindings |
|---|---|
| Use-after-free | Accessing a C object after its finalizer ran |
| Double-free | Calling `moonbit_decref` on an already-released object |
| Memory leaks | Missing finalizer via `moonbit_make_external_object` |
| Buffer overflow | Wrong size passed to `moonbit_make_bytes` |
| Use-after-return | Returning a pointer to a local C variable |

## Quick Start

### Using the Bundled Script (Recommended)

The skill includes `scripts/run-asan.py` which automates the full workflow:

1. Detect platform and find appropriate clang with ASan support. On macOS,
   prefers Homebrew LLVM (supports both ASan and LSan leak detection), falls
   back to system clang (ASan only). On Linux, uses gcc.
2. Set `MOON_CC` and `MOON_AR` env vars to override the compiler/archiver for
   both MoonBit-generated C and stub C compilation. This avoids ASan runtime
   version mismatches between system clang and Homebrew LLVM.
3. Snapshot and patch target package files with ASan flags in `cc-flags`,
   `stub-cc-flags` (appended to existing flags), and `cc-link-flags` (prepended
   to existing flags).
4. Run `moon test --target native -v` with ASan environment variables.
5. Restore original files in `try/finally`, regardless of test outcome.

Both `moon.pkg` (DSL format) and `moon.pkg.json` (JSON format) are supported. The script auto-detects the format based on filename and patches accordingly — DSL files are patched using text manipulation, JSON files using `json` module parsing.

Single package:

```bash
python3 scripts/run-asan.py --repo-root <project-root> --pkg moon.pkg
```

Multiple packages (include ALL packages with `native-stub` or `cc-link-flags`):

```bash
python3 scripts/run-asan.py \
  --repo-root <project-root> \
  --pkg moon.pkg \
  --pkg main/moon.pkg
```

The `--pkg` argument resolves either format automatically: passing
`moon.pkg.json` finds `moon.pkg` if the JSON file doesn't exist, and vice versa.

### Manual Approach

The script uses two mechanisms that can also be applied manually:

**1. `MOON_CC` + `MOON_AR` env vars** — override the compiler and archiver:

```bash
MOON_CC=/opt/homebrew/opt/llvm/bin/clang MOON_AR=/usr/bin/ar moon test --target native -v
```

`MOON_CC` overrides both `cc` and `stub-cc` via moon's `resolve_cc()`. This
ensures all C code (MoonBit-generated and stubs) uses the same compiler and ASan
runtime, avoiding version mismatch errors. `MOON_AR` **must** be set together
with `MOON_CC` — it is ignored without it.

> **Warning:** `MOON_CC` is a compiler path only (e.g., `/usr/bin/cc`). Do NOT
> include flags (e.g., `MOON_CC="clang -fsanitize=address"` will fail — moon
> treats the value as a single executable path).

**2. Package config patching** — add ASan flags:

| Field | What to set | Why |
|---|---|---|
| `cc-flags` | `"-g -fsanitize=address"` | Instruments MoonBit-generated C code |
| `stub-cc-flags` | Append `-g -fsanitize=address` to existing value | Instruments C stub files (preserves `-I`, `-D` flags) |
| `cc-link-flags` | Prepend `-fsanitize=address` to existing value | Links ASan runtime (preserves `-framework`, `-l` flags) |

**Important:** Patch ALL packages that produce executables — both library packages (with `native-stub`) and `is-main`/test packages (with `cc-link-flags`). Always restore originals in a `finally` block.

---

## 2. GitHub Actions Workflow

For CI integration, the script handles everything automatically. Alternatively,
you can set up the steps manually. Both approaches are shown below.

### Using `run-asan.py` (Recommended)

```yaml
sanitizer-check:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v4

    - name: install moonbit
      run: |
        curl -fsSL https://cli.moonbitlang.com/install/unix.sh | bash
        echo "$HOME/.moon/bin" >> $GITHUB_PATH

    - name: moon update
      run: moon update

    - name: run tests with ASan
      run: |
        python3 scripts/run-asan.py \
          --repo-root . \
          --pkg moon.pkg \
          --pkg main/moon.pkg
```

The script automatically handles mimalloc disable, compiler selection, flag
patching, `ASAN_OPTIONS`, and cleanup. On Linux CI, system `gcc` is used with
full leak detection support.

### Manual Approach

Without the script, you must perform all three steps yourself:

1. **Disable mimalloc**: Replace `libmoonbitrun.o` with an empty object. MoonBit bundles mimalloc as its allocator, which intercepts `malloc`/`free` and prevents ASan from tracking allocations.

2. **Patch package config files**: Add ASan flags to every `moon.pkg` (or `moon.pkg.json`) that has `native-stub` or `cc-link-flags`. There is no `MOON_CFLAGS` env var — `MOON_CC` only accepts a compiler path, so flags must go in the package config:

   | Field | What to set | Why |
   |---|---|---|
   | `cc-flags` | `"-g -fsanitize=address -fno-omit-frame-pointer"` | Instruments MoonBit-generated C code |
   | `stub-cc-flags` | Append `-g -fsanitize=address -fno-omit-frame-pointer` | Instruments C stub files (preserve existing `-I`, `-D` flags) |
   | `cc-link-flags` | Prepend `-fsanitize=address` | Links ASan runtime (preserve existing `-framework`, `-l` flags) |

   On macOS, also set `MOON_CC` and `MOON_AR` to avoid ASan runtime version mismatches (see Platform Setup section).

3. **Run tests** with `ASAN_OPTIONS`:
   ```bash
   ASAN_OPTIONS="detect_leaks=1:fast_unwind_on_malloc=0" moon test --target native -v
   ```

4. **Restore** all modified package files and `libmoonbitrun.o` afterward.

Because the manual approach requires patching and restoring multiple files, the script is strongly recommended — especially in CI where cleanup must happen even on test failure.

For Windows CI, use `cl.exe` to compile the dummy object:

```yaml
- name: disable mimalloc
  run: |
    echo "" >dummy_libmoonbitrun.c
    $out_path = Convert-Path ~/.moon/lib/libmoonbitrun.o
    cl.exe dummy_libmoonbitrun.c /c /Fo: $out_path
```

---

## 3. Platform Setup

### macOS

**Homebrew LLVM** is preferred because it supports both ASan and LSan (leak detection). The script probes several versioned formulae automatically (`llvm`, `llvm@18`, `llvm@19`, `llvm@15`, `llvm@13`). Install with `brew install llvm`.

**System clang (Xcode 15+)** is used as a fallback if Homebrew LLVM is not installed. System clang supports ASan but **not** LSan — leak detection will be disabled (`detect_leaks=0`).

**MOON_CC + MOON_AR:** The script sets `MOON_CC` to the chosen clang and `MOON_AR=/usr/bin/ar`. This is necessary because:
- `MOON_CC` overrides both `cc` and `stub-cc` via moon's `resolve_cc()`, ensuring all C code uses the same ASan runtime (avoids `___asan_version_mismatch_check_apple_clang_*` linker errors)
- `MOON_AR` must be set with `MOON_CC` (ignored without it). Moon derives `ar` from the compiler path; Homebrew LLVM has `llvm-ar` but not `ar`, so `MOON_AR=/usr/bin/ar` is needed

**Leak suppressions:** macOS system libraries (libobjc, libdispatch, dyld) have known leaks. Place a `.lsan-suppressions` file at the project root to suppress them.

### Linux

System `gcc` or `clang` on most distributions includes ASan out of the box. On minimal images, install `libasan` (e.g., `apt-get install libasan6`).

---

## 4. Leak Suppressions

Place `.lsan-suppressions` at your project root to ignore known system library leaks:

```
leak:_libSystem_initializer
leak:_objc_init
leak:libdispatch
```

Each `leak:<pattern>` is matched against stack traces. If any frame matches, the leak
is suppressed. The path passed to `LSAN_OPTIONS` **must be absolute**.

Only suppress leaks from system/third-party code you do not control. Never suppress
leaks in your own C stub functions.

---

## 5. Interpreting Results

### heap-use-after-free

Object was freed but still accessed. Check finalizer order and that `moonbit_decref`
is not called too early. Verify raw C pointers do not outlive the MoonBit wrapper.

### double-free

Same pointer freed twice. Ensure each C resource has exactly one owner. Check that
`moonbit_decref` is not called on already-released objects.

### heap-buffer-overflow

Writing past allocated buffer. Check `moonbit_make_bytes` and `moonbit_make_int32_array`
size calculations, especially byte-count vs element-count conversions.

### detected memory leaks

C allocations not freed. Verify every C allocation is wrapped with
`moonbit_make_external_object` so a finalizer is registered for cleanup.

### Fix Workflow

1. Read the ASan stack trace to find the first frame in your C stub code.
2. Identify which external object or buffer is involved.
3. Trace its lifetime: creation, `incref`/`decref` calls, finalizer registration.
4. Fix the root cause and re-run under ASan to confirm.

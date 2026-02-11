# AddressSanitizer (ASan) Validation for MoonBit C Bindings

Reference for detecting memory bugs in C stub code using AddressSanitizer.

---

## 1. Why ASan Matters

C bindings introduce manual memory management invisible to MoonBit's GC. These bugs
silently corrupt memory or leak resources. ASan catches them at runtime:

| Bug Class | Typical Cause in Bindings |
|---|---|
| Use-after-free | Accessing a C object after its finalizer ran |
| Double-free | Calling `moonbit_decref` on an already-released object |
| Memory leaks | Missing finalizer via `moonbit_make_external_object` |
| Buffer overflow | Wrong size passed to `moonbit_make_bytes` |
| Use-after-return | Returning a pointer to a local C variable |

---

## 2. Quick Start

### One-liner (macOS with Homebrew LLVM)

```bash
MOON_CC="$(brew --prefix llvm)/bin/clang -g -fsanitize=address" \
ASAN_OPTIONS=detect_leaks=1 \
LSAN_OPTIONS=suppressions=$(pwd)/.lsan-suppressions \
moon test --target native -v
```

### Environment Variable Approach

From `moonbit-tree-sitter/scripts/test.py`:

```python
env["MOON_CC"] = flags["cc"] + " -g -fsanitize=address"
env["MOON_AR"] = "/usr/bin/ar"
env["ASAN_OPTIONS"] = "detect_leaks=1"
lsan_suppressions = Path(".lsan-suppressions").resolve()
env["LSAN_OPTIONS"] = f"suppressions={lsan_suppressions}"

subprocess.run(
    ["moon", "test", "--target", "native", "-v"], check=True, env=env
)
```

| Variable | Purpose |
|---|---|
| `MOON_CC` | C compiler override. Must include `-fsanitize=address`. |
| `MOON_AR` | Archiver. Set to `/usr/bin/ar` to avoid Homebrew incompatibilities. |
| `ASAN_OPTIONS` | `detect_leaks=1` enables leak detection. |
| `LSAN_OPTIONS` | Points to suppressions file for known system leaks. |

### Alternative: Patching moon.pkg

From `maria/scripts/test.py` -- patch `moon.pkg` temporarily:

```python
moon_pkg_json["link"]["native"] = {
    "cc": str(clang_path),
    "cc-flags": "-g -fsanitize=address -fno-omit-frame-pointer"
}
```

Always restore the original in a `finally` block. Leftover ASan flags break normal builds.

---

## 3. Using the Bundled Script

The skill includes `scripts/run-asan.py` which automates the full workflow:

1. Detect platform and find appropriate clang with ASan support.
2. Snapshot and patch target `moon.pkg` files with ASan flags.
3. Run `moon test --target native -v` with ASan environment variables.
4. Restore original files in `try/finally`, regardless of test outcome.

Single package:

```bash
python3 scripts/run-asan.py --repo-root <project-root> --pkg src/moon.pkg
```

Multiple packages:

```bash
python3 scripts/run-asan.py \
  --repo-root <project-root> \
  --pkg src/moon.pkg \
  --pkg src/internal/moon.pkg
```

---

## 4. GitHub Actions Workflow

For CI integration, disable mimalloc (which interferes with ASan) and run tests with `ASAN_OPTIONS`. Example from `moonbitlang/async`:

```yaml
sanitizer-check:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v6
      with:
        node-version: '>=22.4.0'

    - name: install
      run: |
        curl -fsSL https://cli.moonbitlang.com/install/unix.sh | bash
        echo "$HOME/.moon/bin" >> $GITHUB_PATH

    - name: moon version
      run: |
        moon version --all
        moonrun --version

    - name: moon update
      run: |
        moon update

    - name: disable mimalloc
      run: |
        echo "" >dummy_libmoonbitrun.c
        gcc dummy_libmoonbitrun.c -c -o ~/.moon/lib/libmoonbitrun.o

    - name: run test with address sanitizer
      run: |
        export ASAN_OPTIONS=fast_unwind_on_malloc=0
        moon test
```

**Key steps:**
1. **Disable mimalloc**: MoonBit's default allocator conflicts with ASan. Replace `libmoonbitrun.o` with a dummy object.
2. **Set `ASAN_OPTIONS`**: Use `fast_unwind_on_malloc=0` for more accurate stack traces (slower but clearer).
3. **Run tests**: `moon test` defaults to native backend if `preferred-target` is set.

For Windows, use `cl.exe` instead of `gcc`:

```yaml
- name: disable mimalloc
  run: |
    echo "" >dummy_libmoonbitrun.c
    $out_path = Convert-Path ~/.moon/lib/libmoonbitrun.o
    cl.exe dummy_libmoonbitrun.c /c /Fo: $out_path
```

---

## 5. Platform Setup

### macOS

System clang (Xcode) does **not** include the ASan runtime. Use Homebrew LLVM:

```bash
brew install llvm
```

The bundled script probes several versioned formulae automatically:

```python
llvm_opts = ["llvm", "llvm@18", "llvm@19", "llvm@15", "llvm@13"]
for llvm in llvm_opts:
    llvm_prefix = subprocess.run(
        ["brew", "--prefix", llvm], check=True, text=True, capture_output=True
    ).stdout.strip()
    clang_path = Path(llvm_prefix) / "bin" / "clang"
    if clang_path.exists():
        return {"cc": str(clang_path), "cc-flags": "-g"}
```

### Linux

System `gcc` or `clang` on most distributions includes ASan out of the box:

```bash
MOON_CC="gcc -g -fsanitize=address" moon test --target native -v
```

On minimal images, install `libasan` (e.g., `apt-get install libasan6`).

---

## 6. Leak Suppressions

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

## 7. Interpreting Results

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

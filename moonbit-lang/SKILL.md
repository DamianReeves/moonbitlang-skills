---
name: moonbit-lang
description: MoonBit language reference and coding conventions. Use when writing MoonBit code, asking about syntax, or encountering MoonBit-specific errors, warnings, or user refactoring requests. Covers error handling, FFI, async, and common pitfalls.
---

# MoonBit Language Reference

@reference/fundamentals.md
@reference/error-handling.md
@reference/ffi.md
@reference/async-experimental.md
@reference/package.md
@reference/toml-parser-parser.mbt

## Official Packages

MoonBit has official packages maintained by the team:

- **moonbitlang/x**: Utilities including file I/O (`moonbitlang/x/fs`)
- **moonbitlang/async**: Asynchronous runtime with TCP, HTTP, async queues, async test, and async main

To use these packages:
1. Add the dependency: `moon add moonbitlang/x` or `moon add moonbitlang/async`
2. Import the specific subpackage in `moon.pkg.json`:
   ```json
   {"import": ["moonbitlang/x/fs"]}
   ```

## Common Pitfalls

- Use `func() |> ignore` not `let _ = func()`
- Use `!condition` not `not(condition)`
- Use `inspect(value)` not `inspect!(value)` (deprecated)
- Use `for i in 0..<n` not C-style `for i = 0; i < n; i = i + 1`
- Use `if opt is Some(v) { ... }` for single-branch matching
- Use `arr.clear()` not `while arr.length() > 0 { arr.pop() }`
- Use `s.code_unit_at(i)` not `s[i]` (deprecated)
- Use `pub(all) enum` not factory functions for simple enums
- Use `let mut` only for reassignment, not for mutable containers like Array
- Use `reinterpret_as_uint()` for unsigned ops, `to_int()` for numeric conversion
- Use `try! func() |> ignore` to ignore errors, `raise` to throw
- Use `func() catch { e => ... }` not `match (try? func()) { Ok(_) => ...; Err(_) => ...}`

## Parser Style Reference

When writing a hand-written parser, follow the style in `reference/toml-parser-parser.mbt` (mirrored from `moonbit-community/toml-parser`).

- Prefer `Parser::parse_*` methods that advance a token view (`view`/`update_view`)
- Centralize error reporting (e.g. `Parser::error`) and include token locations
- Keep functions small (`parse_value`, `parse_array`, ...) and separate blocks with `///|`

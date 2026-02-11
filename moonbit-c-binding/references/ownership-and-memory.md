# Ownership Semantics and Memory Management

MoonBit's C FFI has explicit ownership rules governing how GC-managed objects
cross the boundary into C code.

## `#borrow(param)` Semantics

The `#borrow` annotation declares that C only reads the parameter during the
call and does not store a reference. The compiler can optimize accordingly.

### Read-only access

```mbt nocheck
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

///|
#borrow(parser, ranges)
extern "c" fn ts_parser_set_included_ranges(
  parser : Parser,
  ranges : FixedArray[UInt],
) -> Bool = "moonbit_ts_parser_set_included_ranges"
```

### Ref output parameters

`Ref[T]` cells let C write values back. Borrow them since C does not retain the
cell -- it only writes into it.

```mbt nocheck
///|
#borrow(major, minor, patch)
extern "C" fn __llvm_get_version(
  major : Ref[UInt],
  minor : Ref[UInt],
  patch : Ref[UInt],
) = "LLVMGetVersion"

///|
pub fn llvm_get_version() -> (UInt, UInt, UInt) {
  let major = Ref::new(0U)
  let minor = Ref::new(0U)
  let patch = Ref::new(0U)
  __llvm_get_version(major, minor, patch)
  (major.val, minor.val, patch.val)
}
```

## `#owned(param)` Semantics

The `#owned` annotation explicitly declares that ownership of the parameter transfers to C. C must call `moonbit_decref()` when done. Leaving parameters without an ownership annotation is deprecated — new code should use `#owned(param)` explicitly.

**Primitives** (`Int`, `UInt`, `Bool`, `Double`, `Int64`, `UInt64`, `Byte`,
`Float`): passed by value. No ownership concerns — no annotation needed.

**GC-managed objects** (`Bytes`, `String`, `FixedArray[T]`, external objects,
struct wrappers): use `#owned` to transfer ownership to C. C must call `moonbit_decref()` when
done.

## When C Receives Ownership

With `#owned`, C owns each non-primitive parameter and must decref it:

```c
MOONBIT_FFI_EXPORT
int32_t
moonbit_llhttp_execute(llhttp_t *parser, moonbit_bytes_t data) {
  size_t len = strlen((const char *)data);
  llhttp_errno_t errno = llhttp_execute(parser, (const char *)data, len);
  moonbit_decref(parser);  // Decrement after use
  moonbit_decref(data);    // Decrement byte data
  return errno;
}
```

Rules:

- Call `moonbit_decref()` exactly once per owned parameter.
- If storing the object longer-term, decref when the storage is torn down.
- Every early-return path must still decref all owned parameters.

## External Objects with Finalizers

Use `moonbit_make_external_object(finalizer, payload_size)` for C resources that
need cleanup. The finalizer releases the payload resource only -- the GC frees
the container.

```c
typedef struct MoonBitTSParser {
  TSParser *parser;
} MoonBitTSParser;

static inline void
moonbit_ts_parser_delete(void *object) {
  MoonBitTSParser *parser = (MoonBitTSParser *)object;
  ts_parser_delete(parser->parser);  // Release the C resource ONLY
  // Do NOT free the container - GC does that
}

MOONBIT_FFI_EXPORT
MoonBitTSParser *
moonbit_ts_parser_new(void) {
  MoonBitTSParser *parser = (MoonBitTSParser *)moonbit_make_external_object(
    moonbit_ts_parser_delete, sizeof(TSParser *)
  );
  parser->parser = ts_parser_new();
  return parser;
}
```

MoonBit side -- declare an opaque type:

```mbt nocheck
///|
type Parser  // opaque type backed by external object

///|
extern "c" fn ts_parser_new() -> Parser = "moonbit_ts_parser_new"

///|
pub fn Parser::new() -> Parser {
  ts_parser_new()
}
```

The finalizer receives a pointer to the payload region (not the GC header).
Cast it to your struct type and clean up the inner resource.

## Value Types as Bytes

For small C structs with no pointers and no cleanup, use `moonbit_make_bytes()`
to store them as flat GC-managed data. No finalizer needed.

```c
// Settings as flat bytes
MOONBIT_FFI_EXPORT
llhttp_settings_t *
moonbit_llhttp_settings_make(void) {
  llhttp_settings_t *settings =
    (llhttp_settings_t *)moonbit_make_bytes(sizeof(llhttp_settings_t), 0);
  return settings;
}

// Tree-sitter node as flat bytes
static inline MoonBitTSNode *
moonbit_ts_node_new(TSNode node) {
  MoonBitTSNode *self =
    (MoonBitTSNode *)moonbit_make_bytes_sz(sizeof(MoonBitTSNode), 0);
  self->node = node;
  return self;
}
```

MoonBit side -- wrap as a struct over `Bytes`:

```mbt nocheck
///|
struct Settings(Bytes)  // value type backed by GC-managed Bytes

///|
struct Node(Bytes)  // tree-sitter node, small struct, no cleanup
```

Use this pattern when the struct is small, fixed-size, contains no pointers to
heap memory, and is created frequently.

## Memory Pattern Comparison

| Pattern | Use case | C allocation | MoonBit type | Cleanup |
|---|---|---|---|---|
| External object | C handle needing cleanup | `moonbit_make_external_object(finalizer, size)` | `type Foo` (opaque) | Automatic via finalizer |
| Value as Bytes | Small flat struct, no cleanup | `moonbit_make_bytes(size, 0)` | `struct Foo(Bytes)` | GC only, no finalizer |
| Borrowed view | Temporary access during a call | N/A (caller owns) | `#borrow(param)` | No transfer |

### Decision guide

```plaintext
Does the C resource need explicit cleanup (free, close, delete)?
  YES --> External object with finalizer
  NO  --> Is the data a small, flat struct?
            YES --> Value-as-Bytes
            NO  --> External object with finalizer

Will the C function store a reference beyond the call?
  YES --> Use #owned; let C receive ownership
  NO  --> Use #borrow for efficiency
```

## Common Mistakes

- **Forgetting `moonbit_decref`**: every non-borrowed, non-primitive parameter
  is owned. Missing decrefs leak memory.
- **Calling `free()` on external object containers**: the GC manages the
  container. The finalizer only releases the inner resource.
- **Using `#borrow` when C stores the pointer**: the GC may collect the object
  while C holds a stale reference. Only borrow for call-scoped access.
- **Using `moonbit_make_bytes` for structs with inner pointers**: Bytes have no
  finalizer, so inner allocations leak. Use external objects instead.

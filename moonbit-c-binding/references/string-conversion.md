
# String Conversion Strategies

Pass raw `Bytes` across FFI; decode in MoonBit with `@encoding/utf8`. MoonBit
`Bytes` is always null-terminated by the runtime. When passing `Bytes` to C
functions expecting `const char *`, no manual null-termination is needed.

**C side:**

```c
MOONBIT_FFI_EXPORT
moonbit_bytes_t moonbit_ts_node_string(MoonBitTSNode *self, MoonBitTSTree *tree) {
  char *string = ts_node_string(self->node);
  int32_t length = strlen(string);
  moonbit_bytes_t bytes = moonbit_make_bytes(length, 0);
  memcpy(bytes, string, length);
  free(string);
  return bytes;
}
```

**MoonBit side:** Note you need to explicitly import
`moonbitlang/core/encoding/utf8` in order to use the package. The default alias
of this package is `utf8`.

```moonbit
pub fn Node::string(self : Node, tree : Tree) -> String {
  let bytes = ts_node_string(self.node, tree.tree)
  @utf8.decode_lossy(bytes)
}
```

moon.pkg:

```moonbit
import {
  "moonbitlang/core/encoding/utf8"
}
```

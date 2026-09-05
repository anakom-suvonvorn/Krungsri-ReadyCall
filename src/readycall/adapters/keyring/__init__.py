"""Key rings. One today, and it is the dev one (`D110`).

`LocalKeyRing` is imported here because `cryptography` is a base dependency rather than an
extra — encryption that depends on an optional install is encryption that is silently
absent on the machine that skipped it, which is `D14`'s failure mode exactly.
"""

from readycall.adapters.keyring.local import LocalKeyRing, decode_master_key

__all__ = ["LocalKeyRing", "decode_master_key"]

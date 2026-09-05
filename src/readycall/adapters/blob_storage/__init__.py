"""Object-storage adapters, and the encryption that sits in front of all of them.

`S3BlobStorage` is deliberately NOT imported here: it needs the `s3` extra, and this
package is imported on every CI run. `build_blob_storage` imports it at the point of use,
the same shape as `SileroVad` in `adapters/vad/`.
"""

from readycall.adapters.blob_storage.encrypting import EncryptingBlobStorage
from readycall.adapters.blob_storage.factory import build_blob_storage, build_keyring
from readycall.adapters.blob_storage.localfs import LocalFsBlobStorage
from readycall.adapters.blob_storage.memory import InMemoryBlobStorage

__all__ = [
    "EncryptingBlobStorage",
    "InMemoryBlobStorage",
    "LocalFsBlobStorage",
    "build_blob_storage",
    "build_keyring",
]

"""Convert a Hugging Face Whisper checkpoint to the CTranslate2 format faster-whisper wants.

    uv run python scripts/convert_ct2.py
    uv run python scripts/convert_ct2.py --model biodatlab/whisper-th-large-v3-combined

**Why this script exists at all.** `faster-whisper` cannot load a plain transformers
checkpoint — it needs a CTranslate2 build, which is a different file layout. Some models
publish one; **Thonburian does not.** `biodatlab/whisper-th-medium-combined` ships
`model.safetensors` and nothing CTranslate2 can read, so it has to be converted once,
locally, before `STT_ENGINE=thonburian_ct2` can work.

This was found the embarrassing way: the adapter's default was
`biodatlab/whisper-th-medium-combined-ct2`, a model id that **does not exist** and was
assumed rather than checked (`B17`). Verifying it took one HTTP request that should have
been made before the name was written down.

The conversion is a one-off. It downloads the ~3 GB checkpoint, writes a quantised copy to
`models/` (gitignored), and after that everything is local and offline — which is what
`PLAN.md`'s risk register means by *never depend on the venue*.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.console import enable_utf8  # noqa: E402

DEFAULT_HF_MODEL = "biodatlab/whisper-th-medium-combined"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_HF_MODEL, help="the HF checkpoint to convert")
    parser.add_argument("--out", default="", help="output directory (default: models/<name>-ct2)")
    parser.add_argument(
        "--quantization",
        default="int8_float16",
        help="int8_float16 (default, ~4x smaller than fp32 - see D95 on the 4 GiB card), "
        "float16, or int8",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing output")
    args = parser.parse_args()

    enable_utf8()
    name = args.model.rstrip("/").split("/")[-1]
    out = Path(args.out) if args.out else ROOT / "models" / f"{name}-ct2"

    if out.exists() and not args.force:
        print(f"already converted: {out}")
        print("\nUse it with:")
        print(f"  uv run python scripts/bake_off.py --engines faster_whisper:{out} --audio ...")
        print(f"  STT_ENGINE=thonburian_ct2 STT_MODEL={out}")
        return 0

    converter = shutil.which("ct2-transformers-converter")
    if converter is None:
        print("ct2-transformers-converter not found. It ships with faster-whisper:")
        print("  uv sync --extra ml")
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        converter,
        "--model",
        args.model,
        "--output_dir",
        str(out),
        "--quantization",
        args.quantization,
        "--copy_files",
        "tokenizer.json",
        "preprocessor_config.json",
    ]
    if args.force:
        cmd.append("--force")

    print(f"converting {args.model}")
    print(f"        -> {out}  ({args.quantization})")
    print("\nThis downloads the full checkpoint the first time (~3 GB for medium) and then")
    print("writes a quantised copy. Both are on local disk afterwards.\n")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print("\nconversion failed - see the output above")
        return result.returncode

    size_mb = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1024**2
    print(f"\ndone: {out}  ({size_mb:.0f} MB on disk)")
    print("\nUse it with:")
    print(f"  uv run python scripts/bake_off.py --engines faster_whisper:{out} --audio ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

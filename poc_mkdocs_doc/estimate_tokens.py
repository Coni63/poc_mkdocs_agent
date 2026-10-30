"""Estimate the token count of a docs folder.

Usage: python estimate_tokens.py [folder] [--ext .md,.txt]

Uses tiktoken (cl100k_base) if installed, otherwise ~4 chars/token.
Counts are approximate; Claude's tokenizer differs by roughly +/-15%.
"""
import argparse
from pathlib import Path

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def count(text: str) -> int:
        return len(_enc.encode(text, disallowed_special=()))

    METHOD = "tiktoken cl100k_base"
except ImportError:

    def count(text: str) -> int:
        return round(len(text) / 4)

    METHOD = "estimate (chars / 4)"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("folder", nargs="?", default="docs")
    p.add_argument("--ext", default=".md", help="comma-separated extensions")
    args = p.parse_args()

    exts = {e if e.startswith(".") else "." + e for e in args.ext.split(",")}
    root = Path(args.folder)
    rows = []
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix.lower() in exts:
            text = f.read_text(encoding="utf-8", errors="replace")
            rows.append((count(text), len(text), f.relative_to(root)))

    for tokens, _, path in sorted(rows, reverse=True)[:15]:
        print(f"{tokens:>9,}  {path}")
    total = sum(r[0] for r in rows)
    print(f"\n{len(rows)} files, {sum(r[1] for r in rows):,} chars")
    print(f"~{total:,} tokens  [{METHOD}]")


if __name__ == "__main__":
    main()

"""Two useful audits of non-GUI Python lines under app/ only.

Legacy: token-bearing lines, consistent with v0.2's 852-line measurement.
Strict: additionally excludes all module/class/function docstrings.
Comments, blank lines, tests, scripts, templates and UI never count.
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path


IGNORED_TOKENS = {
    tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
    tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING,
}


def code_token_lines(source: str) -> set[int]:
    """Count lines with meaningful tokens, including multiline SQL literals."""
    lines = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type not in IGNORED_TOKENS:
            lines.update(range(tok.start[0], tok.end[0] + 1))
    return lines


def docstring_lines(source: str) -> set[int]:
    """Exclude the first expression statement when it is a Python docstring."""
    found = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue
        value = node.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.update(range(node.body[0].lineno, node.body[0].end_lineno + 1))
    return found


def count_file(path: Path) -> tuple[int, int]:
    source = path.read_text(encoding="utf-8")
    legacy = code_token_lines(source)
    strict = legacy - docstring_lines(source)
    return len(legacy), len(strict)


def main():
    paths = sorted(Path("app").rglob("*.py"))
    total_legacy = 0
    total_strict = 0
    print("LEGACY  STRICT  FILE")
    for path in paths:
        legacy, strict = count_file(path)
        total_legacy += legacy
        total_strict += strict
        print(f"{legacy:6}  {strict:6}  {path}")
    print(f"TOTAL token-bearing (v0.2-compatible): {total_legacy}")
    print(f"TOTAL excluding docstrings (more conservative): {total_strict}")
    print("Excludes tests/, scripts/, GUI, comments and blank lines.")
    print("Neither total is a claimed official course-grader measurement.")


if __name__ == "__main__":
    main()

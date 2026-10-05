"""Edit one entry of a YAML file as text, leaving every other line exactly as it is (comments, layout).

Works on block-style mappings (a key on its own line, its content indented below) and on the items of a
block sequence — which is how the configuration files are written.
"""

from __future__ import annotations

import re

import yaml


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _end(lines: list[str], start: int, hi: int, indent: int) -> int:
    """The line after the block that starts at `start` (a key or item at `indent`)."""
    end = hi
    for i in range(start + 1, hi):
        if lines[i].strip() and indent_of(lines[i]) <= indent and \
                not (lines[i].lstrip().startswith("- ") and indent_of(lines[i]) == indent and
                     not lines[start].lstrip().startswith("- ")):
            end = i
            break
    while end - 1 > start and (not lines[end - 1].strip() or
                               (lines[end - 1].lstrip().startswith("#") and indent_of(lines[end - 1]) <= indent)):
        end -= 1   # blank lines and comments just before the next entry belong to that entry
    return end


def locate(lines: list[str], path: list[str]) -> tuple[int, int, int]:
    """(first line, line after the last, indent) of the mapping entry at `path`. KeyError if absent."""
    lo, hi, indent = 0, len(lines), 0
    start = end = 0
    for depth, key in enumerate(path):
        pat = re.compile(rf"^ {{{indent}}}(['\"]?){re.escape(str(key))}\1:(\s|$)")
        start = next((i for i in range(lo, hi) if pat.match(lines[i])), -1)
        if start < 0:
            raise KeyError(key)
        end = _end(lines, start, hi, indent)
        if depth < len(path) - 1:
            child = next((indent_of(l) for l in lines[start + 1:end] if l.strip() and not l.lstrip().startswith("#")),
                         indent + 2)
            lo, hi, indent = start + 1, end, child
    return start, end, indent


def render(key: str, snippet: str, indent: int) -> list[str]:
    """`key: value` lines for a YAML snippet, at `indent`."""
    value = yaml.safe_load(snippet)
    pad = " " * indent
    text = snippet.strip("\n").removesuffix("\n...").strip("\n")
    if not isinstance(value, (dict, list)) or (len(text.splitlines()) == 1 and text.lstrip()[:1] in "[{"):
        return [f"{pad}{key}: {text.strip()}"]
    return [f"{pad}{key}:"] + [(" " * (indent + 2) + l) if l.strip() else "" for l in text.splitlines()]


def set_entry(text: str, path: list[str], snippet: str | None) -> str:
    """Replace the entry at `path` with a snippet; add it if absent; delete it with snippet None."""
    lines = text.split("\n")
    try:
        start, end, indent = locate(lines, path)
        new = [] if snippet is None else render(path[-1], snippet, indent)
        if snippet is None and start > 0 and not lines[start - 1].strip():
            start -= 1
        lines[start:end] = new
    except KeyError:
        if snippet is None:
            return text
        if len(path) == 1:
            at, indent = len(lines), 0
            while at > 0 and not lines[at - 1].strip():
                at -= 1
        else:
            p_start, at, p_indent = locate(lines, path[:-1])
            indent = next((indent_of(l) for l in lines[p_start + 1:at] if l.strip() and not l.lstrip().startswith("#")),
                          p_indent + 2)
        lines[at:at] = render(path[-1], snippet, indent)
    out = "\n".join(lines)
    yaml.safe_load(out)   # the result must still be YAML
    return out


def items(text: str, key: str) -> tuple[list[str], int, int, list[list[str]]]:
    """The items of the block sequence under top-level `key`: (all lines, first line, line after, item blocks).
    An item block holds the blank lines and comments in front of it."""
    lines = text.split("\n")
    start, end, _ = locate(lines, [key])
    body = lines[start + 1:end]
    item_indent = next((indent_of(l) for l in body if l.lstrip().startswith("- ")), 2)
    blocks: list[list[str]] = []
    pending: list[str] = []
    for l in body:
        if l.lstrip().startswith("- ") and indent_of(l) == item_indent:
            blocks.append(pending + [l])
            pending = []
        elif blocks and l.strip() and not (l.lstrip().startswith("#") and indent_of(l) <= item_indent):
            blocks[-1].extend(pending + [l])
            pending = []
        else:
            pending.append(l)   # blank or a comment between items: goes with the next item
    if pending and blocks:
        blocks[-1].extend(pending)
    return lines, start + 1, end, blocks


def put_items(lines: list[str], lo: int, hi: int, blocks: list[list[str]]) -> str:
    out = lines[:lo] + [l for b in blocks for l in b] + lines[hi:]
    text = "\n".join(out)
    yaml.safe_load(text)
    return text

import re
import logging

logger = logging.getLogger(__name__)

IGNORE_PATTERNS = [
    re.compile(p) for p in [
        r"\.lock$", r"package-lock\.json$", r"yarn\.lock$",
        r"\.min\.(js|css)$", r"(^|/)dist/", r"(^|/)build/",
        r"\.(png|jpe?g|gif|svg|ico|bmp|webp|pdf|zip|tar|gz|mp[34]|exe|dll|so|ttf|woff2?)$",
        r"(^|/)node_modules/", r"(^|/)__pycache__/", r"\.pyc$",
        r"_pb2\.py$", r"_pb2_grpc\.py$",
    ]
]


def should_ignore_file(filename):
    # True if the file matches an ignored pattern (locks, binaries, build, node_modules...)
    return any(pat.search(filename) for pat in IGNORE_PATTERNS)


def parse_diff(diff_text):
    # Parses a unified diff into a files -> hunks -> lines structure (+/-/context with line numbers)
    result = {"files": [], "skipped_files": []}
    current_file = None
    current_hunk = None
    old_lineno = 0
    new_lineno = 0

    file_re = re.compile(r"^diff --git a/(.*?) b/(.*?)$")
    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\s*(.*)$")

    for line in diff_text.split("\n"):

        m = file_re.match(line)
        if m:
            filename = m.group(2)
            if should_ignore_file(filename):
                result["skipped_files"].append(filename)
                current_file = None
                current_hunk = None
            else:
                current_file = {"filename": filename, "hunks": []}
                result["files"].append(current_file)
            continue

        m = hunk_re.match(line)
        if m and current_file:
            current_hunk = {
                "header": m.group(5).strip(),
                "lines": [],
            }
            current_file["hunks"].append(current_hunk)
            old_lineno = int(m.group(1))
            new_lineno = int(m.group(3))
            continue

        if current_hunk:
            if not line:
                current_hunk["lines"].append({"kind": " ", "old": old_lineno, "new": new_lineno, "text": ""})
                old_lineno += 1
                new_lineno += 1
            elif line[0] in ("+", "-", " "):
                kind = line[0]
                text = line[1:]
                if kind == "+":
                    current_hunk["lines"].append({"kind": "+", "old": None, "new": new_lineno, "text": text})
                    new_lineno += 1
                elif kind == "-":
                    current_hunk["lines"].append({"kind": "-", "old": old_lineno, "new": None, "text": text})
                    old_lineno += 1
                else:
                    current_hunk["lines"].append({"kind": " ", "old": old_lineno, "new": new_lineno, "text": text})
                    old_lineno += 1
                    new_lineno += 1

    return result


def get_annotated_text(parsed):
    # Renders the diff as [old=... new=...] + line, directly usable by the LLM
    parts = []
    for f in parsed["files"]:
        added = sum(len(h["lines"]) for h in f["hunks"] for l in h["lines"] if l["kind"] == "+")
        deleted = sum(len(h["lines"]) for h in f["hunks"] for l in h["lines"] if l["kind"] == "-")
        parts.append(f"\n### {f['filename']}  (+{added} / -{deleted})")
        for h in f["hunks"]:
            parts.append(f"@@ {h['header']}")
            for dl in h["lines"]:
                old = str(dl["old"]) if dl["old"] is not None else "~"
                new = str(dl["new"]) if dl["new"] is not None else "~"
                parts.append(f"[old={old} new={new}] {dl['kind']} {dl['text']}")
    return "\n".join(parts)


def get_all_lines(parsed):
    # Flattens all lines of all files (with the file name) for the position map
    lines = []
    for f in parsed["files"]:
        for h in f["hunks"]:
            for dl in h["lines"]:
                lines.append({"file": f["filename"], **dl})
    return lines


def count_modified_lines(parsed):
    # Counts the actually modified lines (+ and -) for the PR size limit
    return sum(
        1
        for f in parsed["files"]
        for h in f["hunks"]
        for dl in h["lines"]
        if dl["kind"] in ("+", "-")
    )

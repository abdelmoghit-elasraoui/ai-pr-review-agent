import logging
from .diff_parser import get_all_lines

logger = logging.getLogger(__name__)


def _find_file(filename, parsed):
    # Localise un fichier dans le diff parsé (tolère les préfixes a/ et b/)
    for f in parsed["files"]:
        if f["filename"] == filename:
            return f
        stripped = filename.removeprefix("a/").removeprefix("b/")
        if stripped == f["filename"]:
            return f
    return None


def _is_line_in_diff(filename, line, parsed):
    # Vrai si le numéro de ligne existe dans le diff (évite les 422 de l'API GitHub)
    file_data = _find_file(filename, parsed)
    if not file_data:
        return False
    for h in file_data["hunks"]:
        for dl in h["lines"]:
            if dl["new"] == line or dl["old"] == line:
                return True
    return False


def _build_position_map(parsed):
    # Convertit les numéros de ligne (fichier, new) en positions attendues par l'API review
    all_lines = get_all_lines(parsed)
    position_map = {}
    position = 0
    current_file = ""

    for dl in all_lines:
        if dl["file"] != current_file:
            current_file = dl["file"]
            position = 0

        position += 1
        if dl["kind"] in ("+", " ") and dl["new"] is not None:
            position_map[(dl["file"], dl["new"])] = position

    return position_map


def build_review(review, parsed):
    # Transforme les findings LLM en review GitHub: commentaires inline valides + findings globaux
    findings = review.get("findings", [])
    summary = review.get("summary", "")

    if not findings:
        body = "## 🤖 AI Code Review\n\n" + (summary or "No issues found.")
        return body, [], "", []

    position_map = _build_position_map(parsed)

    valid = []
    invalid = []
    for f in findings:
        filename = f.get("file", "")
        line = f.get("line")
        if filename and isinstance(line, int) and _is_line_in_diff(filename, line, parsed):
            valid.append(f)
        else:
            invalid.append(f)

    severity_order = {"high": 0, "medium": 1, "low": 2}
    valid.sort(key=lambda x: (severity_order.get(x.get("severity", "low"), 2), x.get("file", "")))

    inline_comments = []
    for f in valid:
        filename = f["file"].removeprefix("a/").removeprefix("b/")
        line = f["line"]
        position = position_map.get((filename, line))
        if position is None:
            invalid.append(f)
            continue

        sev = f.get("severity", "low").upper()
        cat = f.get("category", "general")
        msg = f.get("message", "")
        suggestion = f.get("suggestion", "")

        body = f"**{sev}** — {cat}\n\n{msg}"
        if suggestion:
            body += f"\n\n**Suggestion:**\n\n```suggestion\n{suggestion}\n```"

        inline_comments.append({"path": filename, "position": position, "body": body})

    severity_counts = {}
    for f in findings:
        s = f.get("severity", "low")
        severity_counts[s] = severity_counts.get(s, 0) + 1

    stats = [f"**{c}** {s}" for s, c in severity_counts.items() if c]
    stats_line = "Findings: " + " · ".join(stats) if stats else "No issues found."

    body_parts = ["## 🤖 AI Code Review", "", summary, "", stats_line, ""]
    if inline_comments:
        body_parts.append(f"{len(inline_comments)} inline comment(s) attached to the diff.")
    if invalid:
        body_parts.append(f"{len(invalid)} finding(s) could not be placed on specific lines.")

    overall_body = "\n".join(body_parts)

    global_body = ""
    if invalid:
        global_parts = ["## ⚠️ Findings (unable to attach to specific lines)", ""]
        for f in invalid:
            sev = f.get("severity", "low").upper()
            global_parts.append(f"- **{sev}** `{f.get('file', '?')}:{f.get('line', '?')}` — {f.get('message', '')}")
            if f.get("suggestion"):
                global_parts.append(f"  - Suggestion: {f['suggestion']}")
        global_body = "\n".join(global_parts)
        overall_body = global_body + "\n\n" + overall_body

    logger.info("Review built: %d inline, %d invalid", len(inline_comments), len(invalid))
    return overall_body, inline_comments, global_body, invalid

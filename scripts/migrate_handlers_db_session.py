#!/usr/bin/env python3
"""Замена session = Session(); try/.../finally: close на get_db_session()."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER_FILES = [
    ROOT / "handlers/start.py",
    ROOT / "handlers/attendance_handlers.py",
    ROOT / "handlers/coach_handlers.py",
    ROOT / "handlers/card_handlers.py",
]


def reindent_block(body_lines, extra: int):
    """+extra пробелов только у строк с отступом не меньше первой непустой строки тела."""
    min_indent = None
    for line in body_lines:
        if line.strip():
            min_indent = len(line) - len(line.lstrip())
            break
    if min_indent is None:
        return body_lines
    out = []
    for line in body_lines:
        if not line.strip():
            out.append(line)
            continue
        cur = len(line) - len(line.lstrip())
        if cur >= min_indent:
            out.append(" " * extra + line)
        else:
            out.append(line)
    return out


def transform(content: str) -> str:
    lines = content.splitlines(keepends=True)
    out = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s+)session = Session\(\)\s*$", lines[i])
        if not (m and i + 1 < len(lines) and lines[i + 1].strip() == "try:"):
            out.append(lines[i])
            i += 1
            continue

        indent = m.group(1)
        i += 2

        body = []
        except_lines = []
        while i < len(lines):
            line = lines[i]
            if re.match(r"^" + re.escape(indent) + r"finally:\s*$", line):
                i += 1
                if i < len(lines) and "session.close()" in lines[i]:
                    i += 1
                break
            if re.match(r"^" + re.escape(indent) + r"except\b", line):
                except_lines.append(line)
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    if re.match(
                        r"^" + re.escape(indent) + r"(finally|except)\b", nxt
                    ):
                        break
                    except_lines.append(nxt)
                    i += 1
                continue
            body.append(line)
            i += 1

        if except_lines:
            out.append(f"{indent}try:\n")
            out.append(f"{indent}    with get_db_session() as session:\n")
            out.extend(reindent_block(body, 4))
            out.extend(except_lines)
        else:
            out.append(f"{indent}with get_db_session() as session:\n")
            out.extend(body)
    return "".join(out)


def fix_imports(content: str) -> str:
    if "from core.database import get_db_session" not in content:
        if "from database.db_utils" in content:
            content = content.replace(
                "from database.db_utils",
                "from core.database import get_db_session\nfrom database.db_utils",
                1,
            )
        else:
            content = content.replace(
                "from database.models import",
                "from core.database import get_db_session\nfrom database.models import",
                1,
            )
    content = re.sub(
        r"from database\.models import ([^\n]+)",
        lambda m: "from database.models import "
        + ", ".join(
            p.strip()
            for p in m.group(1).split(",")
            if p.strip() and p.strip() != "Session"
        ),
        content,
        count=1,
    )
    return content


def main():
    for path in HANDLER_FILES:
        raw = path.read_text(encoding="utf-8")
        new = fix_imports(transform(raw))
        if new != raw:
            path.write_text(new, encoding="utf-8")
            print("updated", path.relative_to(ROOT))


if __name__ == "__main__":
    main()

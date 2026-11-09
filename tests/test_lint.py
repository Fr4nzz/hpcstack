"""Regression cases for the bare-placeholder check in scripts/lint.sh.

Each test copies the repository (without .git) to a temporary folder, adds one Markdown file and runs lint there.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", ".scrub-patterns.local")


def lint_errors(tmp_path, markdown):
    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=IGNORE)
    (repo / "case.md").write_text(markdown)
    r = subprocess.run(["bash", "scripts/lint.sh"], cwd=repo, capture_output=True, text=True)
    return [line for line in r.stdout.splitlines() if line.startswith("ERROR: case.md")]


@pytest.mark.parametrize("tag", ["<s>", "<p>", "<q>", "<b>", "<i>", "<u>", "<del>", "<spread>", "<study name>"])
def test_bare_tag_fails(tmp_path, tag):
    errors = lint_errors(tmp_path, f"# Case\n\nSeed spread {tag} here.\n")
    assert len(errors) == 1 and errors[0].startswith("ERROR: case.md:3:"), errors


CLEAN = """# Case

Inline `<s>` and ``a `<p>` b`` are code. An autolink <https://example.org> and 2 < 3 > 1 are text.
A span over two lines `bsub -q
<queue>` is code too.

```bash
bsub -q <q> -n <n>
```

~~~
<s> in a tilde fence
~~~

````markdown
```
<s> inside a four-backtick fence, after an inner three-backtick line
```
````

Paragraph before an indented block.

    <s> in an indented code block

- A list item
    <flagged> is list text, so it is checked
"""


def test_code_is_ignored(tmp_path):
    errors = lint_errors(tmp_path, CLEAN)
    assert len(errors) == 1 and errors[0].startswith("ERROR: case.md:26:"), errors   # list text is prose


def test_stray_backtick_hides_nothing_past_its_paragraph(tmp_path):
    errors = lint_errors(tmp_path, "# Case\n\nA stray ` backtick.\n\nThen <s> here, and a closing ` later.\n")
    assert len(errors) == 1 and errors[0].startswith("ERROR: case.md:5:"), errors


def test_escaped_backticks_open_no_code(tmp_path):
    errors = lint_errors(tmp_path, "# Case\n\nLiteral \\` then <s> then \\`.\n")
    assert len(errors) == 1 and errors[0].startswith("ERROR: case.md:3:"), errors


def test_backtick_in_fence_info_is_no_fence(tmp_path):
    errors = lint_errors(tmp_path, "# Case\n\n```bad`info\n\n<s> here\n")
    assert len(errors) == 1 and errors[0].startswith("ERROR: case.md:5:"), errors

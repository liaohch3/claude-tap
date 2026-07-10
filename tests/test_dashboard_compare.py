"""Unit contracts for the dashboard's side-by-side line comparison."""


def _line_diff_rows(left_text: str, right_text: str) -> list[tuple[str, str, str]]:
    """Mirror dashboard.html lineDiffRows for fast alignment coverage."""
    left = left_text.split("\n")[:800] if left_text else []
    right = right_text.split("\n")[:800] if right_text else []
    matrix = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for left_index in range(1, len(left) + 1):
        for right_index in range(1, len(right) + 1):
            if left[left_index - 1] == right[right_index - 1]:
                matrix[left_index][right_index] = matrix[left_index - 1][right_index - 1] + 1
            else:
                matrix[left_index][right_index] = max(
                    matrix[left_index - 1][right_index],
                    matrix[left_index][right_index - 1],
                )

    rows: list[tuple[str, str, str]] = []
    left_index = len(left)
    right_index = len(right)
    while left_index > 0 or right_index > 0:
        if left_index > 0 and right_index > 0 and left[left_index - 1] == right[right_index - 1]:
            rows.append((left[left_index - 1], right[right_index - 1], "same"))
            left_index -= 1
            right_index -= 1
        elif right_index > 0 and (
            left_index == 0 or matrix[left_index][right_index - 1] > matrix[left_index - 1][right_index]
        ):
            rows.append(("", right[right_index - 1], "added"))
            right_index -= 1
        else:
            rows.append((left[left_index - 1], "", "removed"))
            left_index -= 1
    return _align_modified_rows(list(reversed(rows)))


def _align_modified_rows(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    aligned: list[tuple[str, str, str]] = []
    index = 0
    while index < len(rows):
        if rows[index][2] == "same":
            aligned.append(rows[index])
            index += 1
            continue

        run: list[tuple[str, str, str]] = []
        while index < len(rows) and rows[index][2] != "same":
            run.append(rows[index])
            index += 1
        removed = [row for row in run if row[2] == "removed"]
        added = [row for row in run if row[2] == "added"]
        for offset in range(max(len(removed), len(added))):
            left = removed[offset][0] if offset < len(removed) else ""
            right = added[offset][1] if offset < len(added) else ""
            kind = "modified" if left and right else "removed" if left else "added"
            aligned.append((left, right, kind))
    return aligned


def test_line_diff_rows_keeps_shared_prompt_lines_aligned() -> None:
    rows = _line_diff_rows(
        "You are an agent.\nUse Read.\nAnswer briefly.",
        "You are an agent.\nUse Bash.\nAnswer briefly.",
    )

    assert rows[0] == ("You are an agent.", "You are an agent.", "same")
    assert ("Use Read.", "Use Bash.", "modified") in rows
    assert rows[-1] == ("Answer briefly.", "Answer briefly.", "same")


def test_line_diff_rows_handles_content_present_on_only_one_side() -> None:
    assert _line_diff_rows("", "tool: Research") == [
        ("", "tool: Research", "added"),
    ]

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
    return list(reversed(rows))


def test_line_diff_rows_keeps_shared_prompt_lines_aligned() -> None:
    rows = _line_diff_rows(
        "You are an agent.\nUse Read.\nAnswer briefly.",
        "You are an agent.\nUse Bash.\nAnswer briefly.",
    )

    assert rows[0] == ("You are an agent.", "You are an agent.", "same")
    assert ("Use Read.", "", "removed") in rows
    assert ("", "Use Bash.", "added") in rows
    assert rows[-1] == ("Answer briefly.", "Answer briefly.", "same")


def test_line_diff_rows_handles_content_present_on_only_one_side() -> None:
    assert _line_diff_rows("", "tool: Research") == [
        ("", "tool: Research", "added"),
    ]

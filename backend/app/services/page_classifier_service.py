import re


MATH_SYMBOLS = (
    "=",
    "±",
    "×",
    "÷",
    "√",
    "∑",
    "∫",
    "≤",
    "≥",
    "≈",
)


def has_math_signals(
    text: str,
) -> bool:
    symbol_count = sum(
        text.count(symbol)
        for symbol in MATH_SYMBOLS
    )

    equation_lines = 0

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        if re.search(
            r"[A-Za-z0-9]\s*[=<>±×÷]\s*[A-Za-z0-9]",
            line,
        ):
            equation_lines += 1

    return (
        symbol_count >= 6
        or equation_lines >= 3
    )


def has_table_signals(
    text: str,
) -> bool:
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if len(lines) < 4:
        return False

    table_like_lines = 0

    for line in lines:
        numbers = re.findall(
            r"\d+(?:[.,]\d+)?",
            line,
        )

        if len(numbers) >= 4:
            table_like_lines += 1

    return (
        table_like_lines >= 4
    )


def get_page_image_count(
    page,
) -> int:
    image_count = 0

    try:
        resources = page.get(
            "/Resources"
        )

        if not resources:
            return 0

        resources = (
            resources.get_object()
            if hasattr(
                resources,
                "get_object",
            )
            else resources
        )

        x_object = resources.get(
            "/XObject"
        )

        if not x_object:
            return 0

        x_object = (
            x_object.get_object()
        )

        for obj in (
            x_object.values()
        ):
            try:
                obj = (
                    obj.get_object()
                )

                subtype = obj.get(
                    "/Subtype"
                )

                if subtype == "/Image":
                    image_count += 1

            except Exception:
                continue

    except Exception:
        return 0

    return image_count


def page_has_images(
    page,
) -> bool:
    return (
        get_page_image_count(
            page
        )
        > 0
    )


def has_figure_signals(
    text: str,
) -> bool:
    text = (
        text or ""
    ).lower()

    patterns = (
        r"\bfigure\s+\d+",
        r"\bfig\.\s*\d+",
        r"\bfig\s+\d+",
        r"\bchart\s+\d+",
        r"\bdiagram\s+\d+",
        r"\bgraph\s+\d+",
        r"\bimage\s+\d+",
        r"\billustration\s+\d+",
        r"\bشكل\s*\d+",
        r"\bالشكل\s*\d+",
        r"\bمخطط\s*\d+",
        r"\bالرسم\s+البياني",
    )

    for pattern in patterns:
        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            return True

    return False


def has_dense_layout_signals(
    text: str,
) -> bool:
    lines = [
        line.strip()
        for line in (
            text or ""
        ).splitlines()
        if line.strip()
    ]

    if not lines:
        return False

    short_lines = sum(
        1
        for line in lines
        if len(line) <= 35
    )

    numeric_lines = sum(
        1
        for line in lines
        if re.search(
            r"\d",
            line,
        )
    )

    if (
        len(lines) >= 12
        and short_lines
        / len(lines)
        >= 0.6
    ):
        return True

    if (
        len(lines) >= 10
        and numeric_lines
        / len(lines)
        >= 0.6
    ):
        return True

    return False


def is_complex_page(
    page,
    text: str,
) -> bool:
    text = (
        text or ""
    ).strip()

    text_length = len(
        text
    )

    image_count = (
        get_page_image_count(
            page
        )
    )

    if image_count > 0:
        return True

    if text_length < 60:
        return True

    if has_math_signals(
        text
    ):
        return True

    if has_table_signals(
        text
    ):
        return True

    if has_figure_signals(
        text
    ):
        return True

    if has_dense_layout_signals(
        text
    ):
        return True

    return False
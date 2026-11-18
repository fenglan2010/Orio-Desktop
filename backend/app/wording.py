"""Direction-aware Chinese phrasing for metric changes. Not an API surface."""


def change_verb(a, b, *, invert: bool = False, up: str = "升至", down: str = "降至", flat: str = "持平") -> str:
    try:
        av, bv = float(a), float(b)
    except (TypeError, ValueError):
        return down
    if av == bv:
        return flat
    rose = bv > av
    if invert:
        rose = not rose
    return up if rose else down


def from_to_phrase(a, b, unit: str = "") -> str:
    verb = change_verb(a, b, up="升至", down="降至", flat="持平")
    if verb == "持平":
        return f"由 {a}{unit} 持平"
    return f"由 {a}{unit} {verb} {b}{unit}"


def rank_from_to(a, b) -> str:
    verb = change_verb(a, b, invert=True, up="前移至", down="后移至", flat="持平于")
    return f"从第{a}{verb}第{b}"

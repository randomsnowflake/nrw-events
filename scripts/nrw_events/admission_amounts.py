"""Parse currency-qualified visitor prices; inference and display live elsewhere."""
import re


def admission_amount(price: str) -> float | None:
    """Prefer a positive ticket tier over zero-priced concessions."""
    number = r"(?:\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+[.,]\d{1,2}|\d+)"
    matches = re.finditer(
        rf"(?:^|[^\d.,])(?:({number})\s*(?:[-–—]|bis)\s*)?({number})\s*(?:,-\s*)?(?:€|eur\b|euro\b)",
        price.casefold(),
    )
    amounts = []
    for match in matches:
        for digits in match.groups():
            if digits is None:
                continue
            normalized = digits
            if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", digits):
                normalized = digits.replace(".", "")
            amounts.append(float(normalized.replace(",", ".")))
    positive_amounts = [value for value in amounts if value > 0]
    return min(positive_amounts) if positive_amounts else (0.0 if amounts else None)

"""Parse currency-qualified visitor prices; inference and display live elsewhere."""
import re

# Reduced tiers are labelled before ("ermäßigt 2 €", "Kinder: 4 €") or right
# after the amount ("2 € ermäßigt", "5 € erm."). The regular adult price is the
# event's price; a concession is only used when no regular tier is named.
_CONCESSION = re.compile(
    r"\berm(?:\b|ä|ae)|\breduziert|\bkind(?:er)?\b|\bschüler|\bstud(?:enten|ierende)|"
    r"\bazubis?\b|\bauszubildende|\bsenior|\brentner|\bjugendliche|\bmitglied|"
    r"\bschwerbehindert|\b(?:bonn|köln)-?(?:ausweis|pass)\b|\bsozialpass|gästekarte|early.?bird|late.?ticket"
)
# A tier the source itself calls regular wins over unlabelled time-slot or
# promotion prices ("12 Euro regulär, ...; donnerstags ab 16 Uhr 5 Euro").
_REGULAR = re.compile(r"\bregulär|\bnormal(?:preis)?\b|\berwachsene\b|\bvollzahler|\bvorverkauf|\bvvk\b")


def admission_amount(price: str) -> float | None:
    """Prefer the regular positive ticket tier over concessions and zero prices."""
    text = price.casefold()
    number = r"(?:\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+[.,]\d{1,2}|\d+)"
    matches = list(re.finditer(
        rf"(?:^|[^\d.,])(?:({number})\s*(?:[-–—]|bis)\s*)?({number})\s*(?:,-\s*)?(?:€|eur\b|euro\b)",
        text,
    ))
    amounts = []
    for index, match in enumerate(matches):
        before = re.split(r"[,;/(\n]", text[matches[index - 1].end() if index else 0:match.start()])[-1]
        following = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        # "5 € (Kinder/ermäßigt)" labels the amount; "8 € (erm. 5 €)" opens the next tier.
        label = re.match(r"\s*\(([^()]*)\)", following)
        after = label.group(1) if label else re.split(r"[,;/().\n]", following)[0]
        concession = bool(_CONCESSION.search(before) or _CONCESSION.search(after))
        regular = not concession and bool(_REGULAR.search(before) or _REGULAR.search(after))
        for digits in match.groups():
            if digits is None:
                continue
            normalized = digits
            if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", digits):
                normalized = digits.replace(".", "")
            amounts.append((float(normalized.replace(",", ".")), concession, regular))
    positive_amounts = [value for value, _, _ in amounts if value > 0]
    labelled_regular = [value for value, _, regular in amounts if value > 0 and regular]
    unreduced = [value for value, concession, _ in amounts if value > 0 and not concession]
    return min(labelled_regular or unreduced or positive_amounts) if positive_amounts else (0.0 if amounts else None)

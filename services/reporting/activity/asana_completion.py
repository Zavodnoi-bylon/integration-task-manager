"""Conservative naming evidence within an already verified Asana task_gid.

This helper proposes evidence; it never accepts proposals or writes task state.
"""
import re


def normalize_naming(value):
    return ' '.join(value.replace('|', ' ').replace('**', '').strip().split()).casefold()


def notification_body(text):
    return text.split('\n\n', 1)[1] if '\n\n' in text else ''


def completion_match(assignment_text, comment_text):
    """Caller must verify same task_gid and comment chronology before calling.

    Prefer the actual naming field over operational notes in the title. A
    variant suffix is allowed only when explicitly requested in the assignment;
    all requested variants must be present in this comment.
    """
    description = notification_body(assignment_text)
    names = re.findall(r'(?im)^\s*Нейминг\s*[-–—:]\s*(.+)$', description)
    if not names:
        lines = assignment_text.splitlines()
        names = lines[1:2]
    names = {normalize_naming(name) for name in names}
    body = notification_body(comment_text)
    lines = [normalize_naming(re.split(r'\s+ГЕО\s*[-–—:]', line, flags=re.I)[0])
             for line in body.splitlines() if line.strip()]
    # Only supported, explicitly requested landing variants can extend naming.
    required = {v.casefold() for v in re.findall(r'(?im)^\s*(Wheel|REG)\s*[-–—:]\s*https?://', description)}
    blocks = re.findall(r'(?m)^\s*(\d{6})\s*\n\s*[A-Z]{2,3}\s*\n([^\n]+)', body)
    delivered = {normalize_naming(naming) for _, naming in blocks}
    observed = {variant for variant in required for name in names
                if name + ' ' + variant in delivered}
    exact = bool(names.intersection(lines))
    variants = bool(required) and observed == required
    matched = exact or variants
    destinations = re.findall(r'(?m)^\s*(\d{6})\s*\n\s*[A-Z]{2,3}\s*\n', body) if matched else []
    return {'matched': matched, 'basis': 'description_naming' if exact else 'requested_variants' if variants else None,
            'destination_ids': sorted(set(destinations)), 'missing_variants': sorted(required-observed)}

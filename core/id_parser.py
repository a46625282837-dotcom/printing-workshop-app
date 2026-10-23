import re
from typing import Optional

BLOOD_TYPES = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}

_ARABIC_LABELS = {
    "name": ["الاسم", "الاسم الكامل"],
    "father": ["اسم الاب", "اسم الأب"],
    "grandfather": ["اسم الجد", "اسم الجد الأعلى"],
    "surname": ["اللقب", "اسم العائلة"],
    "national_id": ["رقم البطاقة", "رقم الهوية", "الرقم الوطني", "رقمaybe"],
    "birth_date": ["تاريخ الميلاد"],
    "issue_date": ["تاريخ الاصدار", "تاريخ الإصدار"],
    "blood_type": ["نوع الدم"],
    "address": ["العنوان", "месторождения"],
    "sex": ["الجنس", "ذكر", "أنثى"],
}


def _find_line_containing(lines: list[str], keywords: list[str]) -> Optional[str]:
    for line in lines:
        stripped = line.strip()
        for kw in keywords:
            if kw in stripped:
                after = stripped.split(kw, 1)[-1].strip()
                if after:
                    return after
    return None


def _extract_national_id(text: str) -> Optional[str]:
    patterns = [
        r"[>\-]?\s*(\d{3}\s*\d{4}\s*\d{4}\s*\d{1,3})",
        r"[>\-]?\s*(\d{12,15})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            digits = re.sub(r"\s+", "", m.group(1))
            if 12 <= len(digits) <= 15:
                return digits
    return None


def _extract_date(text: str) -> Optional[str]:
    m = re.search(r"(\d{2}[/-]\d{2}[/-]\d{4})", text)
    return m.group(1) if m else None


def _extract_blood_type(text: str) -> Optional[str]:
    upper = text.upper().replace(" ", "")
    for bt in BLOOD_TYPES:
        if bt in upper:
            return bt
    return None


def _extract_sex(text: str) -> Optional[str]:
    if re.search(r"[Mmذكـورذكر]", text):
        return "ذكر"
    if re.search(r"[Ffأنثىانثىأنثي]", text):
        return "أنثى"
    return None


def parse_id_card(raw_text: str) -> dict:
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]

    fields = {}
    for field_key, keywords in _ARABIC_LABELS.items():
        value = _find_line_containing(lines, keywords)
        if value:
            fields[field_key] = value

    if "national_id" not in fields:
        nid = _extract_national_id(raw_text)
        if nid:
            fields["national_id"] = nid

    if "birth_date" not in fields:
        d = _extract_date(raw_text)
        if d:
            fields["birth_date"] = d

    if "blood_type" not in fields:
        bt = _extract_blood_type(raw_text)
        if bt:
            fields["blood_type"] = bt

    if "sex" not in fields:
        s = _extract_sex(raw_text)
        if s:
            fields["sex"] = s

    fields.setdefault("name", "")
    fields.setdefault("father", "")
    fields.setdefault("grandfather", "")
    fields.setdefault("surname", "")
    fields.setdefault("national_id", "")
    fields.setdefault("birth_date", "")
    fields.setdefault("blood_type", "")
    fields.setdefault("address", "")
    fields.setdefault("sex", "")

    return fields

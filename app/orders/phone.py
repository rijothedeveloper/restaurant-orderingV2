import re

_SEPARATORS = re.compile(r"[\s().\-]")
_INTERNATIONAL = re.compile(r"\+[1-9][0-9]{7,14}")
# North American numbers: area code and exchange both start with 2-9.
_NATIONAL = re.compile(r"1?([2-9][0-9]{2}[2-9][0-9]{6})")


def normalize_phone(raw: str) -> str:
    """The E.164 form of a phone number, e.g. "(415) 555-2671" -> "+14155552671".

    A number without a leading "+" is read as a US or Canadian number.
    Raises ValueError when it is not a plausible phone number.
    """
    number = _SEPARATORS.sub("", raw)
    if _INTERNATIONAL.fullmatch(number):
        return number
    national = _NATIONAL.fullmatch(number)
    if national:
        return "+1" + national.group(1)
    raise ValueError("enter a phone number with its area code")

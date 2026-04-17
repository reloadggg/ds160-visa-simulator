SUPPORTED_VISA_FAMILIES = {
    "b1_b2",
    "f1",
    "j1",
    "m1",
    "h1b",
    "l1a",
    "l1b",
    "o1",
}


def validate_declared_family(declared_family: str | None) -> str | None:
    if declared_family is None:
        return None
    if declared_family not in SUPPORTED_VISA_FAMILIES:
        raise ValueError(f"unsupported declared_family: {declared_family}")
    return declared_family

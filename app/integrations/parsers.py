from pathlib import Path


def extract_text(filename: str, raw_bytes: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return raw_bytes.decode("utf-8")
    if suffix == ".pdf":
        return "pdf extraction pending from parser backend"
    if suffix == ".docx":
        return "docx extraction pending from parser backend"
    return ""

from pathlib import Path


def test_env_example_does_not_define_duplicate_active_keys() -> None:
    keys: list[str] = []
    for raw_line in Path(".env.example").read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0])

    duplicates = {key for key in keys if keys.count(key) > 1}

    assert duplicates == set()


def test_env_example_leaves_database_url_to_runtime_defaults() -> None:
    active_keys = {
        line.split("=", 1)[0]
        for line in Path(".env.example").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }

    assert "DATABASE_URL" not in active_keys


def test_env_example_points_rag_manifest_at_active_docs() -> None:
    values = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in Path(".env.example").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }

    manifest_path = values["RAG_SOURCE_MANIFEST"]
    assert not manifest_path.startswith("docs/superpowers/")
    assert Path(manifest_path).is_file()

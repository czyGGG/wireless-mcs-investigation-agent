"""Build and verify the course submission from an explicit file allowlist."""

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "无线链路调制编码调查Agent"
TOP_LEVEL_FILES = (
    "report.docx", "report.pdf", "README.md", "requirements.txt", "requirements-lock.txt", "requirements-docs.txt",
    ".env.example", ".gitignore", "app.py", ".streamlit/config.toml",
)
SOURCE_DIRECTORIES = ("src", "data", "skills", "tests", "scripts", "docs")
RESULT_FILES = (
    "experiment_config.json", "reference.json", "evaluation.csv", "summary.csv",
    "case_successes.csv", "comparison.png", "report_word_count.json",
    "semantic_review_example.json", "evidence_index.json",
)


def collect_files() -> list[Path]:
    files = [ROOT / name for name in TOP_LEVEL_FILES]
    files.extend(ROOT / "results" / name for name in RESULT_FILES)
    for directory in SOURCE_DIRECTORIES:
        files.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix not in (".pyc", ".pyo"))
    runs = sorted((ROOT / "results" / "runs").glob("*.json"))
    expected = {f"C{case:02d}_{group}_r{repeat}.json"
                for case in range(1, 9) for group in "ABC" for repeat in range(1, 4)}
    if {path.name for path in runs} != expected:
        raise ValueError("The saved formal experiment must contain all 72 runs.")
    files.extend(runs)
    for directory in ("verification", "ui_runs"):
        files.extend(path for path in (ROOT / "results" / directory).glob("*") if path.is_file())
    for path in files:
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f"Invalid submission source: {path.name}")
        if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
            raise ValueError("A private configuration file entered the allowlist.")
    return sorted(set(files))


def build_package() -> Path:
    files = collect_files()
    values = dotenv_values(ROOT / ".env", interpolate=False)
    credentials = {value for value in (
        values.get("DEEPSEEK_API_KEY"), os.environ.get("DEEPSEEK_API_KEY")
    ) if value}
    contents = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in files}
    for name, content in contents.items():
        contains_credential = any(secret.encode() in content for secret in credentials)
        if contains_credential or re.search(rb"sk-[A-Za-z0-9_-]{24,}", content):
            raise ValueError(f"Credential scan failed for {name}")
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}
    evidence_index = json.loads(contents["results/evidence_index.json"])
    for group in evidence_index:
        for item in group["files"]:
            if hashes.get(item["path"]) != item["sha256"]:
                raise ValueError(f"Missing or changed report evidence: {item['path']}")
    manifest = {
        "algorithm": "SHA256",
        "description": "Hashes of every submitted file except this manifest.",
        "files": hashes,
    }
    destination = ROOT / "submission" / f"{ARCHIVE_ROOT}.zip"
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            archive.writestr(f"{ARCHIVE_ROOT}/{name}", content)
        archive.writestr(f"{ARCHIVE_ROOT}/MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity check failed.")
        for name, expected in hashes.items():
            actual = hashlib.sha256(archive.read(f"{ARCHIVE_ROOT}/{name}")).hexdigest()
            if actual != expected:
                raise ValueError(f"Archive content mismatch for {name}")
    temporary.replace(destination)
    print(json.dumps({"archive": str(destination), "files": len(contents) + 1,
                      "bytes": destination.stat().st_size,
                      "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                      "credential_scan": "passed", "manifest_verification": "passed",
                      "report_evidence_verification": "passed"},
                     ensure_ascii=False, indent=2))
    return destination


if __name__ == "__main__":
    build_package()

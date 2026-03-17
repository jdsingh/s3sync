import pytest
from pathlib import Path
import pyrage
from s3sync.crypto import encrypt_file, decrypt_file, generate_test_keypair


def test_encrypt_decrypt_roundtrip(tmp_path: Path) -> None:
    identity, recipient = generate_test_keypair()
    src = tmp_path / "original.txt"
    src.write_text("hello world")

    enc = encrypt_file(src, [recipient], tmp_path / "tmp")
    assert enc.suffix == ".age"
    assert enc.read_bytes() != src.read_bytes()

    out = tmp_path / "decrypted.txt"
    decrypt_file(enc, identity, out)
    assert out.read_text() == "hello world"


def test_encrypt_multiple_recipients(tmp_path: Path) -> None:
    id1, rec1 = generate_test_keypair()
    id2, rec2 = generate_test_keypair()
    src = tmp_path / "data.txt"
    src.write_text("secret")

    enc = encrypt_file(src, [rec1, rec2], tmp_path / "tmp")

    out1 = tmp_path / "dec1.txt"
    decrypt_file(enc, id1, out1)
    assert out1.read_text() == "secret"

    out2 = tmp_path / "dec2.txt"
    decrypt_file(enc, id2, out2)
    assert out2.read_text() == "secret"


def test_encrypted_filename_appends_age(tmp_path: Path) -> None:
    _, recipient = generate_test_keypair()
    src = tmp_path / "report.pdf"
    src.write_bytes(b"\x00" * 100)
    enc = encrypt_file(src, [recipient], tmp_path / "tmp")
    assert enc.name == "report.pdf.age"


def test_temp_dir_created_if_missing(tmp_path: Path) -> None:
    _, recipient = generate_test_keypair()
    src = tmp_path / "f.txt"
    src.write_text("x")
    tmp_dir = tmp_path / "nested" / "tmp"
    enc = encrypt_file(src, [recipient], tmp_dir)
    assert enc.exists()

from pathlib import Path
import pyrage


def generate_test_keypair() -> tuple[pyrage.x25519.Identity, pyrage.x25519.Recipient]:
    """Generate a throwaway keypair for testing only."""
    identity = pyrage.x25519.Identity.generate()
    recipient = identity.to_public()
    return identity, recipient


def encrypt_file(
    src: Path,
    recipients: list[pyrage.x25519.Recipient],
    tmp_dir: Path,
) -> Path:
    """Encrypt src to a temp file. Returns path to the .age temp file."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / (src.name + ".age")
    plaintext = src.read_bytes()
    ciphertext = pyrage.encrypt(plaintext, recipients)
    dest.write_bytes(ciphertext)
    return dest


def decrypt_file(
    enc_path: Path,
    identity: pyrage.x25519.Identity,
    dest: Path,
) -> None:
    """Decrypt an .age file to dest using the given identity."""
    ciphertext = enc_path.read_bytes()
    plaintext = pyrage.decrypt(ciphertext, [identity])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(plaintext)


def load_identity(identity_file: Path) -> pyrage.x25519.Identity:
    """Load an age identity (private key) from file."""
    text = identity_file.read_text().strip()
    return pyrage.x25519.Identity.from_str(text)


def parse_recipient(public_key: str) -> pyrage.x25519.Recipient:
    """Parse an age public key string into a Recipient."""
    return pyrage.x25519.Recipient.from_str(public_key)

import base64, os
from cryptography.fernet import Fernet, InvalidToken

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA_DIR = os.path.join(BASE_DIR, 'data')
KEY_FILE = os.path.join(DATA_DIR, 'enc.key')

def _load_or_create_key() -> bytes:
    os.makedirs(DATA_DIR, exist_ok=True)
    env = os.environ.get('ENCRYPTION_KEY')
    if env:
        return env.encode()
    if os.path.exists(KEY_FILE):
        return open(KEY_FILE,'rb').read()
    key = Fernet.generate_key()
    with open(KEY_FILE,'wb') as f: f.write(key)
    return key

_CIPHER = Fernet(_load_or_create_key())

def enc_str(s: str) -> str:
    return _CIPHER.encrypt(s.encode()).decode()

def dec_str(s: str) -> str:
    try:
        # First, try to decrypt using the key
        return _CIPHER.decrypt(s.encode()).decode()
    except InvalidToken:
        # If decryption fails, it might be old unencrypted data.
        # Try to decode from Base64 as a fallback.
        try:
            return base64.urlsafe_b64decode(s.encode()).decode()
        except Exception:
            # If all else fails, return the original string assuming it's plaintext.
            return s
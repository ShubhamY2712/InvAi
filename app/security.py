"""Passwords, JWT tokens, the current user, and role checks.

Checks SECRET_KEY at import, so the app (which imports this) still refuses to start without it."""
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.config import secret_key
from app.models import UserRole

# --- JWT CONFIGURATION ---
# --- THE DOOR ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

SECRET_KEY_PLACEHOLDER = "replace-with-a-long-random-string"  # the value in .env.example
SECRET_KEY_MIN_LENGTH = 32
_GENERATE_KEY = 'python -c "import secrets; print(secrets.token_urlsafe(48))"'


def validate_secret_key(value: str | None) -> str:
    """Returns the key, or raises RuntimeError if it's missing, the .env.example placeholder, or too short."""
    if not value:
        raise RuntimeError("SECRET_KEY is not set. Add it to your .env file.")
    if value == SECRET_KEY_PLACEHOLDER:
        raise RuntimeError(f"SECRET_KEY is still the .env.example placeholder. Generate one with: {_GENERATE_KEY}")
    if len(value) < SECRET_KEY_MIN_LENGTH:
        raise RuntimeError(f"SECRET_KEY must be at least {SECRET_KEY_MIN_LENGTH} characters. Generate one with: {_GENERATE_KEY}")
    return value


SECRET_KEY = validate_secret_key(secret_key())
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 # The user will stay logged in for 1 hour


# --- MODERN SECURITY ENGINE ---
def get_password_hash(password: str) -> str:
    """Hashes a password directly using the official bcrypt library."""
    # 1. Convert the string password to raw bytes
    pwd_bytes = password.encode('utf-8')
    # 2. Generate a cryptographic salt and hash the password
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(pwd_bytes, salt)
    # 3. Return as a standard string for the database
    return hashed_password.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a typed password against the database hash."""
    password_byte_enc = plain_password.encode('utf-8')
    hashed_password_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_byte_enc, hashed_password_bytes)


def create_access_token(data: dict):
    """Creates a digitally signed JWT token containing user data."""
    to_encode = data.copy()

    # Calculate the exact time the token should expire
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})

    # Cryptographically sign the token using our SECRET_KEY
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# --- THE BOUNCER & IDENTITY VERIFIER ---
def get_current_user(token: str = Depends(oauth2_scheme)):
    """Verifies the JWT and extracts the user data. Rejects invalid tokens."""

    # The standard error we throw if the token is fake, expired, or missing
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # 1. Identity Verification (The Logic): Decode the cryptographic signature
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        # 2. Extract the vital information
        user_id: str = payload.get("sub")
        business_id: str = payload.get("business_id")

        if user_id is None or business_id is None:
            raise credentials_exception

        # 3. The Bouncer Opens the Door: Return the verified identity back to the route
        return {
            "user_id": user_id,
            "business_id": business_id,
            "role": payload.get("role")
        }

    except JWTError:
        # If the signature fails or token is expired, kick them out
        raise credentials_exception


def has_role(current_user: dict, *roles: UserRole) -> bool:
    """Returns True if the token's role matches one of roles (case-insensitive). Never raises on bad token data."""
    role = current_user.get("role") if isinstance(current_user, dict) else None
    allowed = {r.value.lower() for r in roles}
    return isinstance(role, str) and role.lower() in allowed


def require_role(current_user: dict, *allowed_roles: UserRole, detail: str = "You do not have permission to perform this action.") -> None:
    """Raises 403 unless the token's role matches one of allowed_roles (case-insensitive)."""
    if not has_role(current_user, *allowed_roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def require_user_id(current_user: dict) -> int:
    """The token's user id as an int; a token whose subject isn't a user id gets the same 401 as a bad token."""
    user_id = acting_user_id(current_user)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


def acting_user_id(current_user: dict) -> int | None:
    """The token's user id as an int (None if the token's subject isn't numeric)."""
    try:
        return int(current_user["user_id"])
    except (KeyError, TypeError, ValueError):
        return None

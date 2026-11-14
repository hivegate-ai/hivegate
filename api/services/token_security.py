import base64
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class TokenSecurityError(Exception):
    """Exception raised for token security operations."""

    pass


class TokenEncryption:
    """Handles encryption and decryption of token data."""

    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize token encryption with a key.

        Args:
            encryption_key: Base64-encoded encryption key. If None, generates from environment
        """
        if encryption_key:
            logging.info("Initializing TokenEncryption with provided encryption key")
            self._key = encryption_key.encode()
        else:
            # Generate key from environment variable or create a new one
            master_key = os.environ.get("SECRET_TOKEN_ENC_KEY")
            if not master_key:
                logging.error("SECRET_TOKEN_ENC_KEY environment variable not found")
                raise TokenSecurityError(
                    "SECRET_TOKEN_ENC_KEY environment variable is required for token encryption. "
                    "Generate a key using: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' "
                    "and set it as SECRET_TOKEN_ENC_KEY in your environment."
                )

            logging.info("Initializing TokenEncryption with SECRET_TOKEN_ENC_KEY environment variable")
            self._key = master_key.encode()

        # Create Fernet instance
        try:
            if len(self._key) == 44:  # Base64 encoded Fernet key
                logging.info("Using direct Fernet key (44 bytes)")
                self._fernet = Fernet(self._key)
            else:
                logging.info("Deriving key using PBKDF2 (key length != 44 bytes)")
                # Derive key using PBKDF2
                salt = b"agent_api_salt"  # In production, use a random salt stored securely
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                )
                key = base64.urlsafe_b64encode(kdf.derive(self._key))
                self._fernet = Fernet(key)
            logging.info("TokenEncryption initialization completed successfully")
        except Exception as e:
            logging.error(f"Failed to initialize TokenEncryption: {e}")
            raise TokenSecurityError(f"Failed to initialize encryption: {e}")

    def encrypt_secret_json(self, data: Dict[str, Any]) -> str:
        """Encrypt a JSON-serialisable dict without logging anything about its contents.

        encrypt_token_data logs a prefix of well-known token keys; this is for data whose
        keys are user-chosen (e.g. MCP server headers), where any key may hold a secret.
        Same output format as encrypt_token_data.
        """
        try:
            encrypted = self._fernet.encrypt(json.dumps(data).encode())
            return base64.b64encode(encrypted).decode()
        except Exception as e:
            raise TokenSecurityError(f"Encryption failed: {type(e).__name__}")

    def decrypt_secret_json(self, encrypted_data: str) -> Dict[str, Any]:
        """Decrypt what encrypt_secret_json produced, without logging its contents."""
        try:
            decrypted = self._fernet.decrypt(base64.b64decode(encrypted_data.encode()))
            return json.loads(decrypted.decode())
        except Exception as e:
            raise TokenSecurityError(f"Decryption failed: {type(e).__name__}")

    def encrypt_token_data(self, token_data: Dict[str, Any]) -> str:
        """
        Encrypt token data.

        Args:
            token_data: Dictionary containing token information

        Returns:
            Base64-encoded encrypted token data

        Raises:
            TokenSecurityError: If encryption fails
        """
        try:
            # Log token usage info (without sensitive data)
            token_type = token_data.get("token_type", "unknown")
            if "access_token" in token_data:
                access_token_prefix = token_data["access_token"][:10] if token_data["access_token"] else "none"
                logging.info(f"Encrypting token - type: {token_type}, access_token_prefix: {access_token_prefix}...")
            elif "api_key" in token_data:
                api_key_prefix = token_data["api_key"][:10] if token_data["api_key"] else "none"
                logging.info(f"Encrypting token - type: {token_type}, api_key_prefix: {api_key_prefix}...")
            elif "token" in token_data:
                token_prefix = token_data["token"][:10] if token_data["token"] else "none"
                logging.info(f"Encrypting token - type: {token_type}, token_prefix: {token_prefix}...")
            else:
                logging.info(f"Encrypting token - type: {token_type}")

            # Convert to JSON string
            json_data = json.dumps(token_data, default=str)

            # Encrypt the data
            encrypted_data = self._fernet.encrypt(json_data.encode())

            # Return base64 encoded string
            return base64.b64encode(encrypted_data).decode()
        except Exception as e:
            logging.error(f"Failed to encrypt token data: {e}")
            raise TokenSecurityError(f"Encryption failed: {e}")

    def decrypt_token_data(self, encrypted_data: str) -> Dict[str, Any]:
        """
        Decrypt token data.

        Args:
            encrypted_data: Base64-encoded encrypted token data

        Returns:
            Decrypted token data dictionary

        Raises:
            TokenSecurityError: If decryption fails
        """
        try:
            # Decode from base64
            encrypted_bytes = base64.b64decode(encrypted_data.encode())

            # Decrypt the data
            decrypted_bytes = self._fernet.decrypt(encrypted_bytes)

            # Parse JSON
            token_data = json.loads(decrypted_bytes.decode())

            # Log token usage info (without sensitive data)
            token_type = token_data.get("token_type", "unknown")
            if "access_token" in token_data:
                access_token_prefix = token_data["access_token"][:10] if token_data["access_token"] else "none"
                logging.info(f"Decrypting token - type: {token_type}, access_token_prefix: {access_token_prefix}...")
            elif "api_key" in token_data:
                api_key_prefix = token_data["api_key"][:10] if token_data["api_key"] else "none"
                logging.info(f"Decrypting token - type: {token_type}, api_key_prefix: {api_key_prefix}...")
            elif "token" in token_data:
                token_prefix = token_data["token"][:10] if token_data["token"] else "none"
                logging.info(f"Decrypting token - type: {token_type}, token_prefix: {token_prefix}...")
            else:
                logging.info(f"Decrypting token - type: {token_type}")

            return token_data
        except Exception as e:
            logging.error(f"Failed to decrypt token data: {e}")
            raise TokenSecurityError(f"Decryption failed: {e}")


class TokenValidator:
    """Validates token data and checks expiration."""

    @staticmethod
    def is_token_expired(expires_at: Optional[datetime], buffer_seconds: int = 60) -> bool:
        """
        Check if a token is expired or will expire soon.

        Args:
            expires_at: Token expiration datetime (None means never expires)
            buffer_seconds: Seconds before expiry to consider token expired

        Returns:
            True if token is expired or will expire within buffer time
        """
        if expires_at is None:
            return False

        return datetime.utcnow() + timedelta(seconds=buffer_seconds) >= expires_at

    @staticmethod
    def validate_token_type(token_type: str) -> bool:
        """
        Validate token type.

        Args:
            token_type: The token type to validate

        Returns:
            True if valid token type
        """
        valid_types = {"oauth2", "api_key", "jwt"}
        return token_type.lower() in valid_types

    @staticmethod
    def validate_provider(provider: str) -> bool:
        """
        Validate provider name.

        Args:
            provider: The provider name to validate

        Returns:
            True if valid provider
        """
        valid_providers = {"google", "slack", "openai", "anthropic", "github", "microsoft", "zoom", "dropbox", "notion"}
        return provider.lower() in valid_providers

    @staticmethod
    def validate_oauth2_token_data(token_data: Dict[str, Any]) -> bool:
        """
        Validate OAuth2 token data structure.

        Args:
            token_data: Token data dictionary

        Returns:
            True if valid OAuth2 token structure
        """
        required_fields = {"access_token"}
        optional_fields = {"refresh_token", "token_type", "expires_in", "scope", "client_id", "client_secret"}

        # Log what fields are present in the incoming token data
        present_fields = set(token_data.keys())
        logging.info(f"OAuth2 validation - Fields present: {present_fields}")

        # Log specifically about client credentials
        has_client_id = "client_id" in token_data
        has_client_secret = "client_secret" in token_data
        logging.info(
            f"OAuth2 validation - client_id present: {has_client_id}, client_secret present: {has_client_secret}"
        )

        # Check required fields
        missing_required = required_fields - present_fields
        if missing_required:
            logging.warning(f"OAuth2 validation - Missing required fields: {missing_required}")
            return False

        # Check that all fields are either required or optional
        all_fields = required_fields | optional_fields
        invalid_fields = present_fields - all_fields
        if invalid_fields:
            logging.warning(f"OAuth2 validation - Invalid fields detected: {invalid_fields}")
            return False

        logging.info("OAuth2 validation - Token data structure is valid")
        return True

    @staticmethod
    def validate_api_key_token_data(token_data: Dict[str, Any]) -> bool:
        """
        Validate API key token data structure.

        Args:
            token_data: Token data dictionary

        Returns:
            True if valid API key token structure
        """
        required_fields = {"api_key"}
        optional_fields = {"api_secret", "organization_id", "project_id"}

        # Check required fields
        if not all(field in token_data for field in required_fields):
            return False

        # Check that all fields are either required or optional
        all_fields = required_fields | optional_fields
        return all(field in all_fields for field in token_data.keys())

    @staticmethod
    def validate_jwt_token_data(token_data: Dict[str, Any]) -> bool:
        """
        Validate JWT token data structure.

        Args:
            token_data: Token data dictionary

        Returns:
            True if valid JWT token structure
        """
        required_fields = {"token"}
        optional_fields = {"algorithm", "public_key", "issuer", "audience"}

        # Check required fields
        if not all(field in token_data for field in required_fields):
            return False

        # Check that all fields are either required or optional
        all_fields = required_fields | optional_fields
        return all(field in all_fields for field in token_data.keys())

    @classmethod
    def validate_token_data_structure(cls, token_type: str, token_data: Dict[str, Any]) -> bool:
        """
        Validate token data structure based on token type.

        Args:
            token_type: Type of token ('oauth2', 'api_key', 'jwt')
            token_data: Token data dictionary

        Returns:
            True if token data structure is valid for the given type
        """
        if token_type == "oauth2":
            return cls.validate_oauth2_token_data(token_data)
        elif token_type == "api_key":
            return cls.validate_api_key_token_data(token_data)
        elif token_type == "jwt":
            return cls.validate_jwt_token_data(token_data)
        else:
            return False


# Global encryption instance
_encryption_instance: Optional[TokenEncryption] = None


def get_token_encryption() -> TokenEncryption:
    """Get or create the global token encryption instance."""
    global _encryption_instance
    if _encryption_instance is None:
        _encryption_instance = TokenEncryption()
    return _encryption_instance

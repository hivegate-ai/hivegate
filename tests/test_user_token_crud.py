import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.services.token_security import TokenSecurityError
from db.db_models import UserTokenDB
from db.user_token_crud import (
    create_user_token,
    delete_all_user_tokens,
    delete_user_token,
    get_decrypted_token_data,
    get_token_scopes,
    get_tokens_expiring_soon,
    get_user_token,
    get_user_tokens,
    hard_delete_user_token,
    is_token_expired,
    update_user_token_data,
    user_token_exists,
)


class TestUserTokenCRUD(unittest.TestCase):
    """Test user token CRUD operations."""

    def setUp(self):
        """Set up test database."""
        # Create in-memory SQLite database
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # Create SQLite-compatible user_tokens table (avoid ARRAY/JSONB compatibility issues)
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                CREATE TABLE user_tokens (
                    id INTEGER PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    integration_key VARCHAR(100) NOT NULL,
                    provider VARCHAR(50) NOT NULL,
                    token_type VARCHAR(20) NOT NULL,
                    encrypted_token_data TEXT NOT NULL,
                    scopes TEXT,  -- Store as JSON string for SQLite compatibility
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    UNIQUE(user_id, integration_key)
                )
            """)
            )
            conn.commit()

        # Create session
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = SessionLocal()

        # Test data
        self.user_id = "test_user_123"
        self.integration_key = "google"
        self.provider = "google"
        self.token_type = "oauth2"
        self.token_data = {
            "access_token": "ya29.test_access_token",
            "refresh_token": "1//test_refresh_token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        self.scopes = ["https://www.googleapis.com/auth/drive"]

    def tearDown(self):
        """Clean up test database."""
        self.db.close()

    @patch("db.user_token_crud.get_token_encryption")
    def test_create_user_token_new(self, mock_get_encryption):
        """Test creating a new user token."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_get_encryption.return_value = mock_encryption

        # Create token
        expires_at = datetime.utcnow() + timedelta(hours=1)
        user_token = create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
            scopes=self.scopes,
            expires_at=expires_at,
        )

        # Verify token creation
        self.assertIsNotNone(user_token)
        self.assertEqual(user_token.user_id, self.user_id)
        self.assertEqual(user_token.integration_key, self.integration_key)
        self.assertEqual(user_token.provider, self.provider)
        self.assertEqual(user_token.token_type, self.token_type)
        self.assertEqual(user_token.encrypted_token_data, "encrypted_data")
        self.assertEqual(get_token_scopes(user_token), self.scopes)
        self.assertEqual(user_token.expires_at, expires_at)
        self.assertTrue(user_token.is_active)

        # Verify encryption was called
        mock_encryption.encrypt_token_data.assert_called_once_with(self.token_data)

    @patch("db.user_token_crud.get_token_encryption")
    def test_create_user_token_update_existing(self, mock_get_encryption):
        """Test updating an existing user token."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_get_encryption.return_value = mock_encryption

        # Create initial token
        initial_token = create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
        )

        # Update with new data
        new_token_data = {"access_token": "new_access_token", "refresh_token": "new_refresh_token"}
        mock_encryption.encrypt_token_data.return_value = "new_encrypted_data"

        updated_token = create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=new_token_data,
        )

        # Should be the same record, but updated
        self.assertEqual(initial_token.id, updated_token.id)
        self.assertEqual(updated_token.encrypted_token_data, "new_encrypted_data")

    @patch("db.user_token_crud.get_token_encryption")
    def test_create_user_token_concurrent_insert_updates_instead(self, mock_get_encryption):
        """Two creates for the same user and integration race: the loser updates the winner's row.

        Replaces the repo-root test_reproduce_issue.py (#142), which reproduced the old
        "duplicate key value" error by firing concurrent POST /v2/users/{id}/tokens at a live
        server. Here the other request's row lands between this request's lookup and its
        insert, which is the window that error came from.
        """
        from sqlalchemy.orm import Query

        mock_encryption = MagicMock()
        mock_get_encryption.return_value = mock_encryption

        # The other request wins: its row is committed first.
        mock_encryption.encrypt_token_data.return_value = "winner_encrypted_data"
        other_db = sessionmaker(bind=self.engine)()
        winner = create_user_token(
            db=other_db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
        )
        winner_id = winner.id
        other_db.close()

        # This request looked before that commit, so its lookup found nothing.
        real_first = Query.first
        lookups = []

        def first_misses_once(query):
            lookups.append(query)
            return None if len(lookups) == 1 else real_first(query)

        mock_encryption.encrypt_token_data.return_value = "loser_encrypted_data"
        with patch.object(Query, "first", first_misses_once):
            token = create_user_token(
                db=self.db,
                user_id=self.user_id,
                integration_key=self.integration_key,
                provider=self.provider,
                token_type=self.token_type,
                token_data={"access_token": "newer_access_token"},
            )

        # The insert hit the unique constraint, and the retry updated the existing row.
        self.assertEqual(len(lookups), 2)
        self.assertEqual(token.id, winner_id)
        self.assertEqual(token.encrypted_token_data, "loser_encrypted_data")
        self.assertEqual(self.db.query(UserTokenDB).count(), 1)

    def test_create_user_token_invalid_type(self):
        """Test creating user token with invalid token type."""
        with self.assertRaises(ValueError) as context:
            create_user_token(
                db=self.db,
                user_id=self.user_id,
                integration_key=self.integration_key,
                provider=self.provider,
                token_type="invalid_type",
                token_data=self.token_data,
            )
        self.assertIn("Invalid token type", str(context.exception))

    def test_create_user_token_invalid_provider(self):
        """Test creating user token with invalid provider."""
        with self.assertRaises(ValueError) as context:
            create_user_token(
                db=self.db,
                user_id=self.user_id,
                integration_key=self.integration_key,
                provider="invalid_provider",
                token_type=self.token_type,
                token_data=self.token_data,
            )
        self.assertIn("Invalid provider", str(context.exception))

    def test_create_user_token_invalid_data_structure(self):
        """Test creating user token with invalid data structure."""
        invalid_token_data = {"invalid_field": "value"}

        with self.assertRaises(ValueError) as context:
            create_user_token(
                db=self.db,
                user_id=self.user_id,
                integration_key=self.integration_key,
                provider=self.provider,
                token_type=self.token_type,
                token_data=invalid_token_data,
            )
        self.assertIn("Invalid token data structure", str(context.exception))

    @patch("db.user_token_crud.get_token_encryption")
    def test_get_user_token(self, mock_get_encryption):
        """Test getting a user token."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_get_encryption.return_value = mock_encryption

        # Create token
        created_token = create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
        )

        # Get token
        retrieved_token = get_user_token(self.db, self.user_id, self.integration_key)

        # Verify
        self.assertIsNotNone(retrieved_token)
        assert retrieved_token is not None  # For type checker
        self.assertEqual(retrieved_token.id, created_token.id)
        self.assertEqual(retrieved_token.user_id, self.user_id)
        self.assertEqual(retrieved_token.integration_key, self.integration_key)

    def test_get_user_token_not_found(self):
        """Test getting a non-existent user token."""
        token = get_user_token(self.db, "nonexistent_user", "nonexistent_integration")
        self.assertIsNone(token)

    @patch("db.user_token_crud.get_token_encryption")
    def test_get_user_tokens(self, mock_get_encryption):
        """Test getting all tokens for a user."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_get_encryption.return_value = mock_encryption

        # Create multiple tokens
        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key="google",
            provider="google",
            token_type="oauth2",
            token_data=self.token_data,
        )

        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key="openai",
            provider="openai",
            token_type="api_key",
            token_data={"api_key": "sk-test123"},
        )

        # Get all tokens
        tokens = get_user_tokens(self.db, self.user_id)

        # Verify
        self.assertEqual(len(tokens), 2)
        integration_keys = [token.integration_key for token in tokens]
        self.assertIn("google", integration_keys)
        self.assertIn("openai", integration_keys)

    @patch("db.user_token_crud.get_token_encryption")
    def test_get_decrypted_token_data(self, mock_get_encryption):
        """Test decrypting token data."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_encryption.decrypt_token_data.return_value = self.token_data
        mock_get_encryption.return_value = mock_encryption

        # Create token
        user_token = create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
        )

        # Decrypt token data
        decrypted_data = get_decrypted_token_data(user_token)

        # Verify
        self.assertEqual(decrypted_data, self.token_data)
        mock_encryption.decrypt_token_data.assert_called_once_with("encrypted_data")

    @patch("db.user_token_crud.get_token_encryption")
    def test_get_decrypted_token_data_error(self, mock_get_encryption):
        """Test decryption error handling."""
        # Mock encryption with error
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_encryption.decrypt_token_data.side_effect = TokenSecurityError("Decryption failed")
        mock_get_encryption.return_value = mock_encryption

        # Create token
        user_token = create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
        )

        # Decrypt should raise error
        with self.assertRaises(TokenSecurityError):
            get_decrypted_token_data(user_token)

    def test_is_token_expired(self):
        """Test token expiration checking."""
        # Future expiration
        future_time = datetime.utcnow() + timedelta(hours=1)
        token = UserTokenDB(
            user_id="test_user",
            integration_key="test_key",
            provider="test",
            token_type="oauth2",
            encrypted_token_data="encrypted",
            expires_at=future_time,
        )
        self.assertFalse(is_token_expired(token))

        # Near expiration (within buffer)
        near_time = datetime.utcnow() + timedelta(seconds=30)
        token_near = UserTokenDB(
            user_id="test_user",
            integration_key="test_key",
            provider="test",
            token_type="oauth2",
            encrypted_token_data="encrypted",
            expires_at=near_time,
        )
        self.assertTrue(is_token_expired(token_near, buffer_seconds=60))

        # Past expiration
        past_time = datetime.utcnow() - timedelta(hours=1)
        token_past = UserTokenDB(
            user_id="test_user",
            integration_key="test_key",
            provider="test",
            token_type="oauth2",
            encrypted_token_data="encrypted",
            expires_at=past_time,
        )
        self.assertTrue(is_token_expired(token_past))

        # No expiration
        token_no_expiry = UserTokenDB(
            user_id="test_user",
            integration_key="test_key",
            provider="test",
            token_type="oauth2",
            encrypted_token_data="encrypted",
            expires_at=None,
        )
        self.assertFalse(is_token_expired(token_no_expiry))

    @patch("db.user_token_crud.get_token_encryption")
    def test_update_user_token_data(self, mock_get_encryption):
        """Test updating token data."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.side_effect = ["encrypted_data", "new_encrypted_data"]
        mock_get_encryption.return_value = mock_encryption

        # Create initial token
        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
        )

        # Update token data
        new_token_data = {"access_token": "new_access_token", "refresh_token": "new_refresh_token"}
        new_expires_at = datetime.utcnow() + timedelta(hours=2)

        updated_token = update_user_token_data(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            token_data=new_token_data,
            expires_at=new_expires_at,
        )

        # Verify update
        self.assertIsNotNone(updated_token)
        assert updated_token is not None  # For type checker
        self.assertEqual(updated_token.encrypted_token_data, "new_encrypted_data")
        self.assertEqual(updated_token.expires_at, new_expires_at)

    def test_update_user_token_data_not_found(self):
        """Test updating non-existent token."""
        result = update_user_token_data(
            db=self.db,
            user_id="nonexistent_user",
            integration_key="nonexistent_integration",
            token_data={"test": "data"},
        )
        self.assertIsNone(result)

    @patch("db.user_token_crud.get_token_encryption")
    def test_delete_user_token(self, mock_get_encryption):
        """Test soft deleting a user token."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_get_encryption.return_value = mock_encryption

        # Create token
        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
        )

        # Delete token
        success = delete_user_token(self.db, self.user_id, self.integration_key)
        self.assertTrue(success)

        # Verify token is soft deleted
        token = get_user_token(self.db, self.user_id, self.integration_key)
        self.assertIsNone(token)  # Should not be found in active tokens

    def test_delete_user_token_not_found(self):
        """Test deleting non-existent token."""
        success = delete_user_token(self.db, "nonexistent_user", "nonexistent_integration")
        self.assertFalse(success)

    @patch("db.user_token_crud.get_token_encryption")
    def test_hard_delete_user_token(self, mock_get_encryption):
        """Test hard deleting a user token."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_get_encryption.return_value = mock_encryption

        # Create token
        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
        )

        # Hard delete token
        success = hard_delete_user_token(self.db, self.user_id, self.integration_key)
        self.assertTrue(success)

        # Verify token is completely removed
        from sqlalchemy import and_

        token = (
            self.db.query(UserTokenDB)
            .filter(and_(UserTokenDB.user_id == self.user_id, UserTokenDB.integration_key == self.integration_key))
            .first()
        )
        self.assertIsNone(token)

    @patch("db.user_token_crud.get_token_encryption")
    def test_user_token_exists(self, mock_get_encryption):
        """Test checking if user token exists."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_get_encryption.return_value = mock_encryption

        # Initially should not exist
        self.assertFalse(user_token_exists(self.db, self.user_id, self.integration_key))

        # Create token
        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key=self.integration_key,
            provider=self.provider,
            token_type=self.token_type,
            token_data=self.token_data,
        )

        # Should exist now
        self.assertTrue(user_token_exists(self.db, self.user_id, self.integration_key))

        # Soft delete and check again
        delete_user_token(self.db, self.user_id, self.integration_key)
        self.assertFalse(user_token_exists(self.db, self.user_id, self.integration_key))

    @patch("db.user_token_crud.get_token_encryption")
    def test_get_tokens_expiring_soon(self, mock_get_encryption):
        """Test getting tokens that will expire soon."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_get_encryption.return_value = mock_encryption

        # Create token expiring in 30 minutes
        expires_soon = datetime.utcnow() + timedelta(minutes=30)
        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key="google",
            provider="google",
            token_type="oauth2",
            token_data=self.token_data,
            expires_at=expires_soon,
        )

        # Create token expiring in 2 hours
        expires_later = datetime.utcnow() + timedelta(hours=2)
        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key="slack",
            provider="slack",
            token_type="oauth2",
            token_data=self.token_data,
            expires_at=expires_later,
        )

        # Create API key token (doesn't expire)
        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key="openai",
            provider="openai",
            token_type="api_key",
            token_data={"api_key": "sk-test"},
        )

        # Get tokens expiring within 1 hour
        expiring_tokens = get_tokens_expiring_soon(self.db, hours_ahead=1)

        # Should only return the google token
        self.assertEqual(len(expiring_tokens), 1)
        self.assertEqual(expiring_tokens[0].integration_key, "google")

    @patch("db.user_token_crud.get_token_encryption")
    def test_delete_all_user_tokens(self, mock_get_encryption):
        """Test deleting all tokens for a user."""
        # Mock encryption
        mock_encryption = MagicMock()
        mock_encryption.encrypt_token_data.return_value = "encrypted_data"
        mock_get_encryption.return_value = mock_encryption

        # Create multiple tokens
        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key="google",
            provider="google",
            token_type="oauth2",
            token_data=self.token_data,
        )

        create_user_token(
            db=self.db,
            user_id=self.user_id,
            integration_key="openai",
            provider="openai",
            token_type="api_key",
            token_data={"api_key": "sk-test"},
        )

        # Delete all tokens
        count = delete_all_user_tokens(self.db, self.user_id)
        self.assertEqual(count, 2)

        # Verify all tokens are deleted
        tokens = get_user_tokens(self.db, self.user_id)
        self.assertEqual(len(tokens), 0)


if __name__ == "__main__":
    unittest.main()

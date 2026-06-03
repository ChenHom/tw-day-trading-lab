import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from tw_day_trading_lab.live import (
    generate_live_approval_token,
    get_hmac_token_error_reason,
    validate_hmac_token,
    LiveShioajiBrokerAdapter
)

class TestLiveApprovalHMAC(unittest.TestCase):
    """Sprint 4-B: HMAC-SHA256 Live Approval Token hardening tests."""

    def setUp(self):
        self.secret = "super_secure_live_trading_secret_12345"
        self.expected_token_hash = "some_hash"
        # Mock Sinopac API
        self.mock_api = MagicMock()
        self.mock_api.simulation = False

    def test_generate_and_validate_valid_token(self):
        """A freshly generated token should be valid."""
        now = int(time.time())
        token = generate_live_approval_token(self.secret, timestamp=now)
        
        # Should be valid
        self.assertTrue(validate_hmac_token(token, self.secret, current_time_unix=now))
        self.assertIsNone(get_hmac_token_error_reason(token, self.secret, current_time_unix=now))

    def test_expired_token(self):
        """A token older than 300 seconds should be expired and invalid."""
        now = int(time.time())
        # Token generated 301 seconds ago
        old_token = generate_live_approval_token(self.secret, timestamp=now - 301)
        
        self.assertFalse(validate_hmac_token(old_token, self.secret, current_time_unix=now))
        self.assertEqual(
            get_hmac_token_error_reason(old_token, self.secret, current_time_unix=now),
            "manual_approval_token_expired"
        )
        
        # Token generated 301 seconds in the future
        future_token = generate_live_approval_token(self.secret, timestamp=now + 301)
        self.assertFalse(validate_hmac_token(future_token, self.secret, current_time_unix=now))
        self.assertEqual(
            get_hmac_token_error_reason(future_token, self.secret, current_time_unix=now),
            "manual_approval_token_expired"
        )

    def test_invalid_format(self):
        """A token with incorrect format should return invalid format reason."""
        self.assertEqual(
            get_hmac_token_error_reason("just_some_random_string", self.secret),
            "manual_approval_token_invalid_format"
        )
        self.assertEqual(
            get_hmac_token_error_reason("12345678:too:many:colons", self.secret),
            "manual_approval_token_invalid_format"
        )
        self.assertEqual(
            get_hmac_token_error_reason("abc:hash", self.secret),
            "manual_approval_token_invalid_format"
        )

    def test_hash_mismatch(self):
        """A token generated with a different secret should mismatch."""
        now = int(time.time())
        token_wrong_secret = generate_live_approval_token("wrong_secret", timestamp=now)
        
        self.assertFalse(validate_hmac_token(token_wrong_secret, self.secret, current_time_unix=now))
        self.assertEqual(
            get_hmac_token_error_reason(token_wrong_secret, self.secret, current_time_unix=now),
            "manual_approval_token_hash_mismatch"
        )

    def test_adapter_integration(self):
        """Test LiveShioajiBrokerAdapter integrates the HMAC token gate check."""
        now = int(time.time())
        valid_token = generate_live_approval_token(self.secret, timestamp=now)
        
        adapter = LiveShioajiBrokerAdapter(self.mock_api)
        
        # Case 1: Secret missing in environment
        with patch.dict("os.environ", {}, clear=True):
            res = adapter.check_live_execution_gate(
                allow_live_trading=True,
                manual_approval_token=valid_token,
                current_time_unix=now
            )
            self.assertFalse(res.allowed)
            self.assertIn("live_approval_secret_missing", res.blocked_reasons)

        # Case 2: Secret present and token is valid
        with patch.dict("os.environ", {"LIVE_APPROVAL_SECRET": self.secret}):
            res = adapter.check_live_execution_gate(
                allow_live_trading=True,
                manual_approval_token=valid_token,
                current_time_unix=now
            )
            self.assertTrue(res.allowed)

        # Case 3: Secret present but token expired
        expired_token = generate_live_approval_token(self.secret, timestamp=now - 400)
        with patch.dict("os.environ", {"LIVE_APPROVAL_SECRET": self.secret}):
            res = adapter.check_live_execution_gate(
                allow_live_trading=True,
                manual_approval_token=expired_token,
                current_time_unix=now
            )
            self.assertFalse(res.allowed)
            self.assertIn("manual_approval_token_expired", res.blocked_reasons)


if __name__ == "__main__":
    unittest.main()

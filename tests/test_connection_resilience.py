"""Tests for Telegram bot connection resilience after system sleep."""
# ruff: noqa: E402
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import sys
import types
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Set PROJECT_ROOT before importing bot modules
os.environ["PROJECT_ROOT"] = str(Path(__file__).resolve().parents[1])

# Mock config module
from pathlib import Path as _Path
config_module = types.ModuleType("telegram_bot.utils.config")
config_module.config = types.SimpleNamespace(
    telegram_bot_token="test_token",
    network_retry_attempts=3,
    network_retry_delay=5,
    polling_timeout=30,
    bot_data_dir=_Path("/tmp/test_bot"),
    logs_dir=_Path("/tmp/test_bot/logs"),
    session_store_path=_Path("/tmp/test_bot/sessions.json"),
    allowed_user_ids=[],
    draft_update_min_chars=150,
    draft_update_interval=1.0,
    ffmpeg_path=None,
    claude_cli_path=None,
    claude_settings_path=_Path.home() / ".claude" / "settings.json",
)
sys.modules["telegram_bot.utils.config"] = config_module

import telegram.error
from telegram_bot.core.bot import TelegramBot


class TestConnectionResilience(unittest.TestCase):
    """Test connection resilience and retry logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.bot = TelegramBot()

    @patch('telegram_bot.core.bot.Application')
    def test_builder_configures_timeouts(self, mock_app_class):
        """Test that Application.builder() configures proper timeout values."""
        # Setup mock builder chain
        mock_builder = Mock()
        mock_app_class.builder.return_value = mock_builder

        # Make builder methods return self for chaining
        mock_builder.token.return_value = mock_builder
        mock_builder.concurrent_updates.return_value = mock_builder
        mock_builder.get_updates_read_timeout.return_value = mock_builder
        mock_builder.get_updates_connect_timeout.return_value = mock_builder
        mock_builder.get_updates_pool_timeout.return_value = mock_builder
        mock_builder.post_init.return_value = mock_builder
        mock_builder.build.return_value = Mock()

        self.bot.build()

        # Verify timeout methods were called with proper values
        mock_builder.get_updates_read_timeout.assert_called_once_with(30)
        mock_builder.get_updates_connect_timeout.assert_called_once_with(10)
        mock_builder.get_updates_pool_timeout.assert_called_once_with(5)

    @patch('time.sleep')
    def test_network_error_retries_with_rebuild(self, mock_sleep):
        """Test that NetworkError triggers application rebuild and retry."""
        mock_app = Mock()
        call_count = 0

        def mock_run_polling(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise telegram.error.NetworkError("Connection reset")
            # Exit cleanly on second call
            raise telegram.error.InvalidToken("test exit")

        mock_app.run_polling = Mock(side_effect=mock_run_polling)

        build_count = 0
        def mock_build():
            nonlocal build_count
            build_count += 1
            self.bot.application = mock_app

        self.bot.build = mock_build
        self.bot.application = mock_app

        with self.assertRaises(SystemExit):
            self.bot.run()

        # NetworkError triggered rebuild and retry
        self.assertEqual(call_count, 2)
        self.assertGreaterEqual(build_count, 1)
        mock_sleep.assert_called_with(5)

    @patch('time.sleep')
    @patch('time.time')
    def test_rapid_crash_triggers_system_exit(self, mock_time, mock_sleep):
        """Test that repeated rapid polling exits trigger SystemExit."""
        # Each iteration calls time.time() twice: before and after run_polling
        # Uptime of 1s each (< MIN_UPTIME=30) counts as rapid crash
        mock_time.side_effect = list(range(20))

        mock_app = Mock()
        mock_app.run_polling = Mock()  # returns normally (simulates graceful shutdown)

        def mock_build():
            self.bot.application = mock_app

        self.bot.build = mock_build
        self.bot.application = mock_app

        with self.assertRaises(SystemExit):
            self.bot.run()

        # Should exit after MAX_RAPID_CRASHES (5) consecutive rapid exits
        self.assertEqual(mock_app.run_polling.call_count, 5)


if __name__ == '__main__':
    unittest.main()

import unittest
from unittest.mock import AsyncMock, MagicMock

from uploader.douyin_uploader.main import _extract_douyin_qrcode_src, _wait_for_session_cookie


class DouyinLoginTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_is_returned_without_waiting_for_network_idle_or_tab_text(self):
        image = MagicMock()
        image.wait_for = AsyncMock()
        image.get_attribute = AsyncMock(return_value='data:image/png;base64,abc')
        locator = MagicMock()
        locator.first = image
        page = MagicMock()
        page.locator.return_value = locator
        page.get_by_text.side_effect = AssertionError('QR extraction waited for login tab text')
        page.wait_for_load_state.side_effect = AssertionError('QR extraction waited for network idle')

        result = await _extract_douyin_qrcode_src(page)

        self.assertEqual(result, 'data:image/png;base64,abc')
        page.wait_for_load_state.assert_not_called()
        page.get_by_text.assert_not_called()
        image.wait_for.assert_awaited_once()

    async def test_session_cookie_is_saved_as_soon_as_it_arrives(self):
        context = MagicMock()
        context.cookies = AsyncMock(side_effect=[[], [{'name': 'sessionid', 'value': 'present'}]])
        self.assertTrue(await _wait_for_session_cookie(context, timeout=1))
        self.assertEqual(context.cookies.await_count, 2)

    async def test_missing_session_cookie_fails_without_treating_redirect_as_login(self):
        context = MagicMock()
        context.cookies = AsyncMock(return_value=[])
        self.assertFalse(await _wait_for_session_cookie(context, timeout=0))


if __name__ == '__main__':
    unittest.main()

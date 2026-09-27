"""Cover-pair mapping and late-rendered thumbnail verification, no network."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uploader.tencent_uploader import main as tencent
from uploader.tencent_uploader import cover_controls


class DualCoverChecks(unittest.IsolatedAsyncioTestCase):
    def app(self):
        return tencent.TencentVideo('test', 'unused.mp4', [], 0, 'unused.json',
            thumbnail_landscape_path='horizontal.jpg', thumbnail_portrait_path='vertical.jpg',
            require_thumbnail=True, cover_mode='custom')

    async def test_distinct_horizontal_then_vertical_file_mapping(self):
        app=self.app();app.set_single_thumbnail=AsyncMock()
        await app.set_thumbnail(MagicMock())
        calls=app.set_single_thumbnail.await_args_list
        self.assertEqual([c.args[1] for c in calls],['horizontal.jpg','vertical.jpg'])
        self.assertEqual([c.args[-1] for c in calls],['4:3 横版','3:4 竖版'])
        self.assertEqual(app.cover_mode,'custom')

    async def test_horizontal_failure_never_continues_or_changes_cover_mode(self):
        app=self.app();app.set_single_thumbnail=AsyncMock(side_effect=RuntimeError('横版失败'))
        with self.assertRaisesRegex(RuntimeError,'横版失败'):
            await app.set_thumbnail(MagicMock())
        app.set_single_thumbnail.assert_awaited_once()
        self.assertEqual(app.cover_mode,'custom')

    async def test_late_rendered_card_still_requires_postsave_verification(self):
        app=self.app();root=MagicMock();saved={'sources':['new'],'signature':'new','loaded':True}
        before={'sources':['old'],'signature':'old','loaded':True}
        control=SimpleNamespace(find_cover=AsyncMock(side_effect=[None,root]),verify_saved_preview=AsyncMock())
        app.open_thumbnail_dialog=AsyncMock(return_value=MagicMock())
        app.upload_thumbnail_in_dialog=AsyncMock(return_value=saved)
        with patch.object(cover_controls,'CoverControls',return_value=control), patch.object(cover_controls,'preview_state',AsyncMock(return_value=before)):
            await app.set_single_thumbnail(MagicMock(),'horizontal.jpg',['.horizontal-cover-wrap'],['编辑封面'],'4:3 横版')
        control.verify_saved_preview.assert_awaited_once_with(['.horizontal-cover-wrap'],before,saved)

    async def test_missing_orientation_area_never_skips_verification(self):
        app=self.app();control=SimpleNamespace(find_cover=AsyncMock(return_value=None),verify_saved_preview=AsyncMock())
        app.open_thumbnail_dialog=AsyncMock(return_value=MagicMock())
        app.upload_thumbnail_in_dialog=AsyncMock()
        with patch.object(cover_controls,'CoverControls',return_value=control):
            with self.assertRaisesRegex(RuntimeError,'无法确认当前编辑器对应'):
                await app.set_single_thumbnail(MagicMock(),'horizontal.jpg',['.horizontal-cover-wrap'],['编辑封面'],'4:3 横版')
        app.upload_thumbnail_in_dialog.assert_not_awaited()
        control.verify_saved_preview.assert_not_awaited()

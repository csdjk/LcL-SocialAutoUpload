"""Explicit publication boundary; uploading media is not submitting a post."""
import inspect

STAGES = {
    'checking': '检查账号与素材', 'deduplicating': '检查后台是否已有本期作品',
    'opening': '打开投稿页面', 'uploading': '上传视频', 'metadata': '填写文案与声明',
    'verification': '请在官方窗口完成手机验证', 'cover': '设置封面', 'submitting': '提交投稿', 'verifying': '读取后台结果',
}

class SubmissionNotStarted(RuntimeError):
    """Use only while the final publish action has NEVER been attempted."""
    def __init__(self, message, *, stage='preparing', upload_started=False, code='prepare_failed'):
        super().__init__(message)
        self.stage = stage
        self.upload_started = upload_started
        self.code = code

class PublicationProgress:
    def __init__(self, callback=None):
        self.callback = callback
        self.stage = 'checking'
        self.upload_started = False
        self.submission_started = False
        self.warnings = []

    async def emit(self, stage=None, message=None, *, media_percent=None, **evidence):
        if stage: self.stage = stage
        value = {'stage': self.stage, 'message': message or STAGES.get(self.stage, self.stage),
                 'upload_started': self.upload_started, 'submission_started': self.submission_started,
                 'warnings': list(self.warnings)}
        value.update(evidence)
        if type(media_percent) is int and 0 <= media_percent <= 100:
            value['media_percent'] = media_percent
        if self.callback:
            result = self.callback(value)
            if inspect.isawaitable(result): await result
        return value

    async def warning(self, message):
        if message not in self.warnings: self.warnings.append(message)
        await self.emit(message=message)

    def failure(self, message, code='prepare_failed'):
        if self.submission_started:
            return RuntimeError('已尝试提交，结果需从后台确认；不会自动重复点击发表。')
        error = SubmissionNotStarted(message, stage=self.stage, upload_started=self.upload_started, code=code)
        error.account_file = getattr(self, 'account_file', None)
        return error

import test from 'node:test'
import assert from 'node:assert/strict'
import { isUnuploadedFailure, dailyTaskStatusText, dailyTaskNotice, canPublishDailyPlatform } from '../src/utils/dailyPublishState.js'

const item = { state: 'failed', failure_stage: 'preflight', upload_started: false,
  failure_code: 'login_check_failed', next_action: 'relogin', error: '视频号登录校验未通过，尚未上传。请重新登录。',
  account: {account_id:'web:wechat_channels:1', web_account_id:1}, access_status:'needs_login',
  account_mismatch:false, remote_id:null, retry_allowed:true }

test('login preflight failure explicitly shows not uploaded, not unknown', () => {
  assert.equal(isUnuploadedFailure(item),true)
  assert.equal(dailyTaskStatusText(item),'登录校验失败')
  assert.match(dailyTaskNotice(item),/尚未上传/)
  assert.equal(canPublishDailyPlatform('wechat_channels',item),false)
})
test('after successful re-login failed preflight is manually retryable', () => {
  assert.equal(canPublishDailyPlatform('wechat_channels',{...item,access_status:'ready'}),true)
})
test('unknown upload results remain unknown even with the same error message', () => {
  const unknown={...item,state:'unknown',failure_stage:null,upload_started:null,next_action:null,retry_allowed:false,access_status:'ready'}
  assert.equal(isUnuploadedFailure(unknown),false)
  assert.equal(dailyTaskStatusText(unknown),'结果待核对')
  assert.equal(canPublishDailyPlatform('wechat_channels',unknown),false)
  assert.ok(dailyTaskNotice(unknown))
})
test('confirmed failure without preflight proof still permits manual reconciliation UI', () => {
  assert.equal(isUnuploadedFailure({...item,failure_stage:null}),false)
  assert.equal(dailyTaskStatusText({...item,failure_stage:null}),'确认失败')
})
test('other failed preflight reasons say not uploaded and success has no error notice', () => {
  assert.equal(dailyTaskStatusText({...item,failure_code:'package_invalid'}),'未上传')
  assert.equal(dailyTaskNotice({...item,state:'published'}),'')
  assert.equal(isUnuploadedFailure(null),false)
})

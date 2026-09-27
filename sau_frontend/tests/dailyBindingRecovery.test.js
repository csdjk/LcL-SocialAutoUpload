import test from 'node:test'
import assert from 'node:assert/strict'
import { dailyTaskStatusText, dailyTaskNotice, dailyTaskNoticeType, needsDailyRelogin,
  isRecoveredLoginFailure, canPublishDailyPlatform } from '../src/utils/dailyPublishState.js'

const old = { state:'failed', failure_stage:'preflight', upload_started:false,
  failure_code:'login_check_failed', next_action:'relogin', error:'旧登录校验失败，尚未上传',
  access_status:'needs_login', retry_allowed:true, account_mismatch:false, remote_id:null,
  task_id:'old-task', task_account_id:'web:wechat_channels:1',
  account:{web_account_id:1, account_id:'web:wechat_channels:1'} }

test('deleted binding is an account selection problem rather than failed new login',()=>{
  const item={...old,access_status:'binding_missing'}
  assert.equal(dailyTaskStatusText(item),'请选择发布账号')
  assert.match(dailyTaskNotice(item),/记录已不存在/)
  assert.equal(dailyTaskNoticeType(item),'warning')
  assert.equal(needsDailyRelogin(item),false)
  assert.equal(canPublishDailyPlatform('wechat_channels',item),false)
})

test('successful re-login separates current readiness from historical failed task',()=>{
  const item={...old,access_status:'ready'}
  const snapshot=JSON.stringify(item)
  assert.equal(isRecoveredLoginFailure(item),true)
  assert.equal(dailyTaskStatusText(item),'待重新提交')
  assert.match(dailyTaskNotice(item),/上次任务未上传/)
  assert.match(dailyTaskNotice(item),/提交前仍会检查登录态/)
  assert.equal(dailyTaskNoticeType(item),'info')
  assert.equal(needsDailyRelogin(item),false)
  assert.equal(canPublishDailyPlatform('wechat_channels',item),true)
  assert.equal(JSON.stringify(item),snapshot)
})

test('explicit replacement preserves old audit identity but enables manual retry',()=>{
  const item={...old,access_status:'ready',account:{web_account_id:4,account_id:'web:wechat_channels:4'}}
  assert.equal(dailyTaskStatusText(item),'待重新提交')
  assert.equal(item.task_account_id,'web:wechat_channels:1')
  assert.equal(canPublishDailyPlatform('wechat_channels',item),true)
})

test('unknown result cannot become retryable even after login or binding changes',()=>{
  for (const access_status of ['ready','binding_missing']) {
    const item={...old,state:'unknown',access_status,upload_started:null}
    assert.equal(dailyTaskStatusText(item),'结果待核对')
    assert.equal(isRecoveredLoginFailure(item),false)
    assert.equal(canPublishDailyPlatform('wechat_channels',item),false)
  }
})

test('other-account blocking history cannot be hidden by newer credentials',()=>{
  const item={...old,access_status:'ready',account_mismatch:true}
  assert.equal(isRecoveredLoginFailure(item),false)
  assert.equal(canPublishDailyPlatform('wechat_channels',item),false)
})

test('remote id is never described as unuploaded recovery',()=>{
  const item={...old,access_status:'ready',remote_id:'remote-work'}
  assert.equal(isRecoveredLoginFailure(item),false)
  assert.equal(canPublishDailyPlatform('wechat_channels',item),false)
})

test('real login failures still require login and material problems do not become recovered',()=>{
  assert.equal(dailyTaskStatusText(old),'登录校验失败')
  assert.equal(needsDailyRelogin(old),true)
  const material={...old,access_status:'ready',failure_code:'package_invalid',next_action:'check_material'}
  assert.equal(isRecoveredLoginFailure(material),false)
  assert.equal(dailyTaskStatusText(material),'未上传')
})

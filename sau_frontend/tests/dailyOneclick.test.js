import test from 'node:test'
import assert from 'node:assert/strict'
import { canPublishDailyPlatform, dailyTaskStatusText, dailyTaskNotice } from '../src/utils/dailyPublishState.js'
const base={account:{account_id:'web:wechat_channels:7',web_account_id:7},access_status:'ready',account_mismatch:false,remote_id:null}

test('ready one-click platform is publishable without a selection step',()=>{
  assert.equal(canPublishDailyPlatform('wechat_channels',{...base,state:'ready'}),true)
})
test('known preparation failure is retryable but never described as not uploaded',()=>{
  const item={...base,state:'failed',upload_started:true,submission_started:false,retry_allowed:true,error:'封面编辑未完成'}
  assert.equal(canPublishDailyPlatform('wechat_channels',item),true)
  assert.equal(dailyTaskStatusText(item),'未提交，可重试')
  assert.match(dailyTaskNotice(item),/未执行最终发表/)
  assert.doesNotMatch(dailyTaskNotice(item),/未上传/)
})
test('uncertain final click does not unlock retry',()=>{
  assert.equal(canPublishDailyPlatform('wechat_channels',{...base,state:'unknown',submission_started:true}),false)
})
test('official backend record is distinct from a claim of public publication',()=>{
  const item={...base,state:'processing',remote_id:'real-record',remote_verified:true}
  assert.equal(dailyTaskStatusText(item),'后台已有作品')
  assert.equal(canPublishDailyPlatform('wechat_channels',item),false)
  assert.match(dailyTaskNotice(item),/阻止重复投稿/)
})
test('active task reports its actual stage',()=>{
  assert.equal(dailyTaskStatusText({...base,state:'uploading',progress_message:'设置封面'}),'设置封面')
  assert.equal(canPublishDailyPlatform('wechat_channels',{...base,state:'uploading'}),false)
})

test('durable queued task is visible and cannot be resubmitted',()=>{
  const item={...base,state:'queued'}
  assert.equal(dailyTaskStatusText(item),'已排队')
  assert.match(dailyTaskNotice(item),/本机队列/)
  assert.equal(canPublishDailyPlatform('wechat_channels',item),false)
})

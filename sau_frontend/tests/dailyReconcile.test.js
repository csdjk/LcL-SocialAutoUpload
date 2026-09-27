import test from 'node:test'
import assert from 'node:assert/strict'
import { NO_WORK_NOTE, validateReconcileForm, buildReconcileRequest, buildStatusConfirmation } from '../src/utils/dailyReconcile.js'
import { apiErrorMessage } from '../src/utils/requestErrors.js'
import request from '../src/utils/request.js'

const form = {state:'failed', remote_absent:true, remote_id:'', url:'', note:NO_WORK_NOTE}
const target = {task_id:'fixture-old-task', platform:'wechat_channels', account_id:'web:wechat_channels:4', updated_at:'fixed-time'}

test('status confirmation submits only the choice and original task version', () => {
  assert.deepEqual(buildStatusConfirmation(target,'published'),{
    task_id:target.task_id,state:'published',expected_updated_at:'fixed-time',confirmed:true
  })
  assert.throws(()=>buildStatusConfirmation(null,'published'))
  assert.throws(()=>buildStatusConfirmation({...target,updated_at:null},'published'))
  assert.throws(()=>buildStatusConfirmation(target,'invalid'))
})

test('exact screenshot scenario identifies missing evidence locally', () => {
  assert.ok(validateReconcileForm({...form,note:''}).note)
  assert.ok(validateReconcileForm({...form,note:' \n '}).note)
})
test('explicit no-work statement submits no ID or URL and carries original task', () => {
  const body = buildReconcileRequest(target,form,'2026-09-24T12:00:00+08:00')
  assert.equal(body.task_id,target.task_id)
  assert.equal(body.evidence.account_id,target.account_id)
  assert.equal(body.expected_updated_at,'fixed-time')
  assert.equal(body.evidence.note,NO_WORK_NOTE)
  assert.equal(body.evidence.remote_absent,true)
  assert.equal(body.evidence.remote_id,''); assert.equal(body.evidence.url,'')
})
test('checkbox is never inferred from note', () => {
  assert.ok(validateReconcileForm({...form,remote_absent:false}).remote_absent)
  assert.throws(()=>buildReconcileRequest(target,{...form,remote_absent:false}))
})
test('hidden stale fields cannot contradict explicit failure', () => {
  const body=buildReconcileRequest(target,{...form,remote_id:'old-work',url:'https://example.test/old'})
  assert.equal(body.evidence.remote_id,''); assert.equal(body.evidence.url,'')
})
test('processing and published require real ID; published requires HTTPS', () => {
  for (const state of ['processing','published']) assert.ok(validateReconcileForm({...form,state}).remote_id)
  assert.ok(validateReconcileForm({...form,state:'published',remote_id:'id'}).url)
  for (const url of ['https://','http://example.test','https://user:pass@example.test/']) {
    assert.ok(validateReconcileForm({...form,state:'published',remote_id:'id',url}).url)
  }
  assert.deepEqual(validateReconcileForm({...form,state:'published',remote_id:'id',url:'https://example.test/1'}),{})
})
test('unknown does not require a work ID but still requires a note', () => {
  assert.deepEqual(validateReconcileForm({...form,state:'unknown'}),{})
  const result=buildReconcileRequest(target,{...form,state:'unknown'})
  assert.equal(result.evidence.remote_absent,false)
})
test('invalid target or oversized note cannot submit', () => {
  assert.throws(()=>buildReconcileRequest(null,form))
  assert.ok(validateReconcileForm({...form,note:'x'.repeat(2001)}).note)
})
test('HTTP400 preserves server reason, not generic Axios or network text', () => {
  assert.equal(apiErrorMessage({response:{status:400,data:{msg:'请填写核对依据'}}}),'请填写核对依据')
  assert.match(apiErrorMessage({response:{status:400,data:'<html>bad</html>'}}),/提交内容/)
  assert.match(apiErrorMessage({response:{status:409,data:{}}}),/已更新/)
})
test('only unavailable or timed-out requests are connection errors', () => {
  assert.match(apiErrorMessage({code:'ERR_NETWORK'}),/无法连接本机/)
  assert.match(apiErrorMessage({code:'ECONNABORTED'}),/超时/)
  assert.match(apiErrorMessage({response:{status:500}}),/服务器/)
})
test('actual Axios interceptor returns backend reason and preserves response without logging payload', async () => {
  const descriptor=Object.getOwnPropertyDescriptor(globalThis,'localStorage')
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>null}})
  const original=console.error; const logged=[]; console.error=(...args)=>logged.push(args)
  try {
    await assert.rejects(request.post('/fixture',{}, {silentError:true, adapter:async config=>{
      const error=new Error('Request failed with status code 400')
      error.response={status:400,data:{code:400,msg:'请填写核对依据'}}; error.config=config
      throw error
    }}), error => error.message==='请填写核对依据' && error.response.status===400)
    await assert.rejects(request.post('/fixture',{}, {silentError:true, adapter:async config=>({
      status:200,data:{code:400,msg:'核对账号不匹配'},config,headers:{},statusText:'OK'
    })}), /核对账号不匹配/)
    assert.equal(logged.length,0)
  } finally {
    console.error=original
    if(descriptor) Object.defineProperty(globalThis,'localStorage',descriptor)
    else delete globalThis.localStorage
  }
})

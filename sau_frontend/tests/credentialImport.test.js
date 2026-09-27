import test from 'node:test'
import assert from 'node:assert/strict'
import { importDouyinCredentials } from '../src/utils/credentialImport.js'
const input = { name: ' 测试抖音 ', credentials: '{"cookies":[]}' }

test('new accounts do not require a name; the server returns the platform nickname', async () => {
  const row = [9, 3, 'generated.json', '平台昵称', 1]
  const result = await importDouyinCredentials({ credentials: input.credentials }, { fetchImpl: async (_, config) => {
    assert.equal(JSON.parse(config.body).name, '')
    assert.equal(JSON.parse(config.body).account_id, null)
    return { ok: true, json: async () => ({ code: 200, data: row }) }
  } })
  assert.deepEqual(result, row)
})

test('Edge Cookie header is forwarded unchanged in POST body, never in the URL', async () => {
  const credentials = 'Cookie: sessionid=synthetic-fixture-only; other=a=b=='
  const row = [7, 3, 'fixture.json', '测试抖音', 1]
  const result = await importDouyinCredentials({ ...input, credentials }, {
    baseUrl: 'http://127.0.0.1:5409',
    fetchImpl: async (url, config) => {
      assert.equal(url, 'http://127.0.0.1:5409/accounts/import-douyin')
      assert.equal(JSON.parse(config.body).credentials, credentials)
      assert.equal(config.method, 'POST')
      assert.equal(config.headers.Cookie, undefined)
      return { ok: true, json: async () => ({ code: 200, data: row }) }
    }
  })
  assert.deepEqual(result, row)
})

test('posts credentials in body only with local marker and no caching', async () => {
  const row = [1,3,'test.json','测试抖音',1]
  const result = await importDouyinCredentials(input, { baseUrl:'http://localhost:5409/', fetchImpl: async (url, config) => {
    assert.equal(url, 'http://localhost:5409/accounts/import-douyin')
    assert.equal(config.headers['X-SAU-Local'], '1')
    assert.equal(config.cache, 'no-store')
    assert.deepEqual(JSON.parse(config.body), {name:'测试抖音',credentials:input.credentials,account_id:null})
    return { ok:true, json:async()=>({code:200,data:row}) }
  }})
  assert.deepEqual(result,row)
})
test('replacement explicitly carries an existing account ID', async () => {
  await importDouyinCredentials({...input,accountId:4}, { fetchImpl:async (_, config)=>{
    assert.equal(JSON.parse(config.body).account_id,4)
    return {ok:true,json:async()=>({code:200,data:[]})}
  }})
})
test('validation errors are shown instead of treating any HTTP response as success', async () => {
  await assert.rejects(importDouyinCredentials(input,{ fetchImpl:async()=>({ok:false,json:async()=>({code:422,msg:'需完成官方验证'})})}),/需完成官方验证/)
})
test('empty input and oversized input never contact backend', async () => {
  const options={ fetchImpl:()=>{throw new Error('must not be called')} }
  await assert.rejects(importDouyinCredentials({...input,credentials:''},options),/请选择|请粘贴/)
  await assert.rejects(importDouyinCredentials({...input,credentials:'x'.repeat(512*1024+1)},options),/512 KB/)
})
test('timeout terminates the request and does not claim a successful import', async () => {
  await assert.rejects(importDouyinCredentials(input,{timeoutMs:5,fetchImpl:async (_,config)=>new Promise((_,reject)=>{
    config.signal.addEventListener('abort',()=>reject(new Error('aborted')))
  })}),/刷新账号列表确认结果/)
})
test('network errors are generic, without exposing request configuration', async () => {
  await assert.rejects(importDouyinCredentials(input,{fetchImpl:async()=>{throw new TypeError('network fixture')}}),/无法连接本机/)
})

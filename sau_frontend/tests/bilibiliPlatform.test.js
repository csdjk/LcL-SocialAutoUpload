import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeAccounts, selectableAccountIds, platformTypes } from '../src/stores/account.js'
import { importPlatformCredentials, importDouyinCredentials } from '../src/utils/credentialImport.js'

test('Bilibili mapping, string IDs and cross-platform filtering', () => {
  assert.equal(platformTypes[5], 'B站')
  const accounts = normalizeAccounts([['9','5','bili.json','B站测试','1'],[10,3,'douyin.json','抖音测试',1],[11,5,'expired.json','过期',0]])
  assert.equal(accounts[0].platform,'B站')
  assert.deepEqual(selectableAccountIds(accounts,[9,10,11],'5'),[9])
})
test('Bilibili import uses explicit local endpoint without leaking credentials to URL', async () => {
  const credentials='SESSDATA=synthetic-test; bili_jct=synthetic-csrf'
  const row=[8,5,'bili.json','B站测试',1]
  const result=await importPlatformCredentials({name:'B站测试',credentials,platform:'bilibili'}, {
    baseUrl:'http://127.0.0.1:5409', fetchImpl:async(url,options)=>{
      assert.equal(url,'http://127.0.0.1:5409/accounts/import-bilibili')
      assert.equal(JSON.parse(options.body).credentials,credentials)
      assert.equal(options.headers['X-SAU-Local'],'1')
      return {ok:true,json:async()=>({code:200,data:row})}
    }
  })
  assert.deepEqual(result,row)
})
test('platform must be explicitly supported', async () => {
  await assert.rejects(importPlatformCredentials({platform:'../douyin',name:'test',credentials:'test'},{fetchImpl:()=>{throw Error('should not request')}}),/不支持/)
})
test('existing Douyin caller remains on Douyin route', async () => {
  await importDouyinCredentials({name:'test',credentials:'test'}, {fetchImpl:async(url)=>{
    assert.ok(url.endsWith('/accounts/import-douyin'))
    return {ok:true,json:async()=>({code:200,data:[]})}
  }})
})

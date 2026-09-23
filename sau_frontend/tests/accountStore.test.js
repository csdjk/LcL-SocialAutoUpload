import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { accountApi } from '../src/api/account.js'
import { useAccountStore, normalizeAccounts, selectableAccountIds } from '../src/stores/account.js'

const row = (id = 1, type = 2, status = 1, file = 'test.json') => [id, type, file, '一游解忧', status]
const response = rows => ({ code: 200, data: rows })
function store() { setActivePinia(createPinia()); return useAccountStore() }

test('normalizes numeric strings without turning valid accounts into abnormal', () => {
  const [account] = normalizeAccounts([row('1', '2', '1')])
  assert.equal(account.id, 1); assert.equal(account.type, 2)
  assert.equal(account.platform, '视频号'); assert.equal(account.status, '正常')
})
test('fresh store loads accounts without first visiting account management', async t => {
  t.mock.method(accountApi, 'getAccounts', async () => response([row()]))
  const s = store(); assert.equal(s.accounts.length, 0)
  await s.loadAccounts()
  assert.equal(s.accounts[0].name, '一游解忧'); assert.equal(s.accounts[0].status, '正常')
  assert.equal(s.isLoading, false); assert.equal(s.loadError, '')
})
test('concurrent list requests are deduplicated', async t => {
  let finish, count = 0
  t.mock.method(accountApi, 'getAccounts', () => { count++; return new Promise(resolve => { finish = resolve }) })
  const s = store(); const a = s.loadAccounts(); const b = s.loadAccounts()
  await Promise.resolve(); assert.equal(s.isLoading, true); assert.equal(count, 1)
  finish(response([row()])); await Promise.all([a, b])
  assert.equal(s.accounts.length, 1); assert.equal(s.isLoading, false)
})
test('failed loading keeps cache, resets spinner, and permits retry', async t => {
  let fail = true
  t.mock.method(accountApi, 'getAccounts', async () => {
    if (fail) throw new Error('offline')
    return response([row(2)])
  })
  const s = store(); s.setAccounts([row()])
  await assert.rejects(s.loadAccounts()); assert.equal(s.accounts.length, 1)
  assert.equal(s.isLoading, false); assert.ok(s.loadError)
  fail = false; await s.loadAccounts(); assert.equal(s.accounts[0].id, 2); assert.equal(s.loadError, '')
})
test('validation failure cannot leave accounts permanently validating', async t => {
  t.mock.method(accountApi, 'getValidAccounts', async () => { throw new Error('timeout') })
  const s = store(); s.setAccounts([row()])
  await assert.rejects(s.validateAccounts())
  assert.equal(s.accounts[0].status, '正常'); assert.equal(s.isValidating, false)
  assert.match(s.validationError, /保留上次状态/)
})
test('partial validation errors retain the previous status', async t => {
  t.mock.method(accountApi, 'getValidAccounts', async () => ({ ...response([row(1, 2, 0)]), validation_errors: [{ id: 1 }] }))
  const s = store(); s.setAccounts([row()]); await s.validateAccounts()
  assert.equal(s.accounts[0].status, '正常'); assert.ok(s.validationError)
})
test('older validation never removes a newly added account or overwrites a new cookie', async t => {
  let finish
  t.mock.method(accountApi, 'getValidAccounts', () => new Promise(resolve => { finish = resolve }))
  const s = store(); s.setAccounts([row()]); const pending = s.validateAccounts()
  await Promise.resolve(); s.setAccounts([row(1, 2, 1, 'new.json'), row(2)])
  finish(response([row(1, 2, 0)])); await pending
  assert.equal(s.accounts.length, 2); assert.equal(s.accounts[0].filePath, 'new.json')
  assert.equal(s.accounts[0].status, '正常')
})
test('explicit valid and invalid results both update status', async t => {
  t.mock.method(accountApi, 'getValidAccounts', async () => response([row(1, 2, 0), row(2, 2, 1)]))
  const s = store(); s.setAccounts([row(), row(2, 2, 0)]); await s.validateAccounts()
  assert.deepEqual(s.accounts.map(a => a.status), ['异常', '正常'])
})
test('selection rejects missing, expired and other-platform account IDs', () => {
  const accounts = normalizeAccounts([row(1), row(2, 3), row(3, 2, 0)])
  assert.deepEqual(selectableAccountIds(accounts, ['1', 1, 2, 3, 999], '2'), [1])
  assert.deepEqual(selectableAccountIds(accounts, [1], 3), [])
})

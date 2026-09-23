import { defineStore } from 'pinia'
import { ref } from 'vue'
import { accountApi } from '../api/account.js'

export const platformTypes = { 1: '小红书', 2: '视频号', 3: '抖音', 4: '快手' }

export function normalizeAccounts(rows) {
  return rows.map(item => {
    const row = Array.isArray(item)
      ? { id: item[0], type: item[1], filePath: item[2], name: item[3], status: item[4] }
      : item
    const status = Number(row.status)
    return {
      id: Number(row.id), type: Number(row.type), filePath: row.filePath,
      name: row.name ?? row.userName ?? '',
      status: status === 1 || row.status === '正常' ? '正常'
        : status === 0 || row.status === '异常' ? '异常' : '待验证',
      platform: platformTypes[Number(row.type)] || '未知'
    }
  })
}

export function selectableAccountIds(accounts, selectedIds, platform) {
  const allowed = new Set(accounts.filter(account =>
    account.type === Number(platform) && account.status === '正常' && account.filePath
  ).map(account => account.id))
  return [...new Set(selectedIds.map(Number))].filter(id => allowed.has(id))
}

export const useAccountStore = defineStore('account', () => {
  const accounts = ref([])
  const isLoading = ref(false)
  const loadError = ref('')
  const isValidating = ref(false)
  const validationError = ref('')
  let loadPromise = null
  let validationPromise = null

  const setAccounts = (rows) => { accounts.value = normalizeAccounts(rows) }

  // Every page can fetch the authoritative list; overlapping callers share one request.
  // Do not persist credentials in localStorage or convert saved validity to "loading".
  const loadAccounts = () => {
    if (loadPromise) return loadPromise
    isLoading.value = true
    loadError.value = ''
    loadPromise = Promise.resolve().then(() => accountApi.getAccounts()).then(res => {
      if (res.code !== 200 || !Array.isArray(res.data)) throw new Error('账号列表返回异常')
      setAccounts(res.data)
      return accounts.value
    }).catch(error => {
      loadError.value = '账号列表加载失败，请检查后端服务后重试'
      throw error
    }).finally(() => {
      isLoading.value = false
      loadPromise = null
    })
    return loadPromise
  }

  // Validation is independent from list loading. Never remove newly added accounts
  // or overwrite a re-login's new credentials with an older validation response.
  const validateAccounts = () => {
    if (validationPromise) return validationPromise
    isValidating.value = true
    validationError.value = ''
    validationPromise = Promise.resolve().then(() => accountApi.getValidAccounts()).then(res => {
      if (res.code !== 200 || !Array.isArray(res.data)) throw new Error('账号校验返回异常')
      const updates = normalizeAccounts(res.data)
      const unknown = new Set((res.validation_errors || []).map(item => Number(item.id)))
      accounts.value = accounts.value.map(account => {
        const checked = updates.find(item => item.id === account.id && item.filePath === account.filePath)
        return checked && !unknown.has(account.id) ? { ...account, status: checked.status } : account
      })
      if (unknown.size) validationError.value = '部分账号校验未完成，已保留上次状态，可稍后重试'
      return res
    }).catch(error => {
      validationError.value = '账号校验未完成，已保留上次状态，请稍后重试'
      throw error
    }).finally(() => {
      isValidating.value = false
      validationPromise = null
    })
    return validationPromise
  }

  const addAccount = (account) => { accounts.value.push(account) }
  const updateAccount = (id, updatedAccount) => {
    const index = accounts.value.findIndex(acc => acc.id === Number(id))
    if (index !== -1) accounts.value[index] = { ...accounts.value[index], ...updatedAccount }
  }
  const deleteAccount = (id) => {
    accounts.value = accounts.value.filter(acc => acc.id !== Number(id))
  }
  const getAccountsByPlatform = (platform) => accounts.value.filter(acc => acc.platform === platform)

  return {
    accounts, isLoading, loadError, isValidating, validationError,
    setAccounts, loadAccounts, validateAccounts, addAccount, updateAccount,
    deleteAccount, getAccountsByPlatform
  }
})

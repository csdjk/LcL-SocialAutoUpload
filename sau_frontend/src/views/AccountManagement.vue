<template>
  <div class="account-management">
    <div class="page-header">
      <p class="eyebrow">发布账号</p>
      <h1>账号管理</h1>
      <p class="page-description">管理各平台登录状态，添加或更新你的发布账号。</p>
    </div>
    
    <div class="account-tabs">
      <el-tabs v-model="activeTab" class="account-tabs-nav">
        <el-tab-pane v-for="tab in accountTabs" :key="tab.name" :label="tab.label" :name="tab.name">
          <div class="account-list-container">
            <div class="account-search">
              <el-input
                v-model="searchKeyword"
                placeholder="输入名称或账号搜索"
                prefix-icon="Search"
                clearable
                @clear="handleSearch"
                @input="handleSearch"
              />
              <div class="action-buttons">
                <el-button type="primary" @click="handleAddAccount">添加账号</el-button>
                <el-button type="info" @click="fetchAccounts" :loading="appStore.isAccountRefreshing">
                  <el-icon :class="{ 'is-loading': appStore.isAccountRefreshing }"><Refresh /></el-icon>
                  <span v-if="appStore.isAccountRefreshing">刷新中</span>
                </el-button>
              </div>
            </div>
            
            <div v-if="accountsForTab(tab.label).length > 0" class="account-list">
              <el-table :data="accountsForTab(tab.label)" class="desktop-account-table" style="width: 100%">
                <el-table-column label="头像" width="80">
                  <template #default="scope">
                    <el-avatar class="account-avatar" :size="40">{{ scope.row.name?.slice(0, 2) || '账号' }}</el-avatar>
                  </template>
                </el-table-column>
                <el-table-column prop="name" label="名称" width="180" />
                <el-table-column prop="platform" label="平台" min-width="110">
                  <template #default="scope">
                    <el-tag
                      :type="getPlatformTagType(scope.row.platform)"
                      effect="plain"
                    >
                      {{ scope.row.platform }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="status" label="状态" min-width="100">
                  <template #default="scope">
                    <el-tag
                      :type="getStatusTagType(scope.row.status)"
                      effect="plain"
                      :class="{'clickable-status': isStatusClickable(scope.row.status)}"
                      @click="handleStatusClick(scope.row)"
                    >
                      <el-icon :class="scope.row.status === '验证中' ? 'is-loading' : ''" v-if="scope.row.status === '验证中'">
                        <Loading />
                      </el-icon>
                      {{ scope.row.status }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="操作" min-width="200">
                  <template #default="scope">
                    <el-button size="small" @click="handleReLogin(scope.row)">重新登录</el-button>
                    <el-dropdown @command="handleAccountCommand($event, scope.row)">
                      <el-button size="small">更多</el-button>
                      <template #dropdown><el-dropdown-menu>
                        <el-dropdown-item command="edit">编辑账号</el-dropdown-item>
                        <el-dropdown-item command="download">导出登录态</el-dropdown-item>
                        <el-dropdown-item command="upload">导入登录态</el-dropdown-item>
                        <el-dropdown-item command="delete" divided>删除账号</el-dropdown-item>
                      </el-dropdown-menu></template>
                    </el-dropdown>
                  </template>
                </el-table-column>
              </el-table>
              <div class="mobile-account-list">
                <article v-for="account in accountsForTab(tab.label)" :key="account.id" class="mobile-account-card">
                  <div class="mobile-account-head"><strong>{{ account.name }}</strong><el-tag :type="getStatusTagType(account.status)" effect="plain">{{ account.status }}</el-tag></div>
                  <p>{{ account.platform }}</p>
                  <div class="mobile-account-actions">
                    <el-button type="primary" @click="handleReLogin(account)">重新登录</el-button>
                    <el-dropdown @command="handleAccountCommand($event, account)">
                      <el-button>更多</el-button>
                      <template #dropdown><el-dropdown-menu>
                        <el-dropdown-item command="edit">编辑账号</el-dropdown-item>
                        <el-dropdown-item command="download">导出登录态</el-dropdown-item>
                        <el-dropdown-item command="upload">导入登录态</el-dropdown-item>
                        <el-dropdown-item command="delete" divided>删除账号</el-dropdown-item>
                      </el-dropdown-menu></template>
                    </el-dropdown>
                  </div>
                </article>
              </div>
            </div>
            
            <div v-else class="empty-data">
              <el-empty :description="tab.name === 'all' ? '暂无账号数据' : `暂无${tab.label}账号数据`" />
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
    
    <!-- 添加/编辑账号对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogType === 'add' ? '添加账号' : dialogType === 'import' ? `更新${accountForm.platform}登录态` : dialogType === 'relogin' ? '重新登录' : '编辑账号'"
      width="min(500px, calc(100vw - 32px))"
      align-center
      style="max-height: calc(100dvh - 32px); overflow-y: auto"
      :close-on-click-modal="false"
      :close-on-press-escape="!sseConnecting && !credentialBusy"
      :show-close="!sseConnecting && !credentialBusy"
    >
      <el-form :model="accountForm" label-width="80px" :rules="rules" ref="accountFormRef">
        <el-form-item label="平台" prop="platform">
          <el-select 
            v-model="accountForm.platform" 
            placeholder="请选择平台" 
            style="width: 100%"
            :disabled="dialogType !== 'add' || sseConnecting || credentialBusy"
            @change="handleLoginPlatformChange"
          >
            <el-option label="快手" value="快手" />
            <el-option label="抖音" value="抖音" />
            <el-option label="视频号" value="视频号" />
            <el-option label="小红书" value="小红书" />
            <el-option label="B站" value="B站" />
            <el-option label="YouTube" value="YouTube" />
            <el-option label="今日头条" value="今日头条" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="dialogType !== 'add'" label="名称" prop="name">
          <el-input 
            v-model="accountForm.name" 
            placeholder="请输入账号名称" 
            :disabled="sseConnecting || credentialBusy || dialogType === 'import'"
          />
        </el-form-item>
        <p v-if="dialogType === 'add'" class="browser-login-note">登录成功后，自动使用平台账号昵称。</p>
        <el-form-item v-if="accountForm.platform && ['add', 'relogin'].includes(dialogType)" label="登录方式">
          <el-radio-group v-model="loginMode" size="small" :disabled="sseConnecting || credentialBusy" @change="resetLoginMethod">
            <el-radio-button value="browser">{{ accountForm.platform === 'YouTube' ? 'Google 授权' : '浏览器登录' }}</el-radio-button>
            <el-radio-button v-if="!['YouTube', '今日头条'].includes(accountForm.platform) && (dialogType === 'add' || accountForm.platform === 'B站')" value="qr">界面扫码</el-radio-button>
            <el-radio-button v-if="['抖音', 'B站'].includes(accountForm.platform)" value="import">导入登录态</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <YouTubeOAuthSetup v-if="dialogVisible && accountForm.platform === 'YouTube' && ['add', 'relogin'].includes(dialogType) && !sseConnecting && loginStatus !== '200'"
          @ready="youtubeConfigured = $event" @busy="credentialBusy = $event" />
        <div v-if="accountForm.platform && accountForm.platform !== 'YouTube' && loginMode === 'browser' && ['add', 'relogin'].includes(dialogType) && !sseConnecting && !loginStatus" class="browser-login-intro">
          <p>在普通 Edge 窗口完成登录，工具会自动验证并保存账号。</p>
          <p class="browser-login-note">无需扩展。登录状态会保留，下次打开同一账号时继续使用；平台要求验证时再按提示登录。</p>
        </div>
        <DouyinCredentialImport v-if="dialogVisible && isCredentialImport && loginStatus !== '200'"
          :key="accountForm.platform" :platform="accountForm.platform === 'B站' ? 'bilibili' : 'douyin'"
          :name="accountForm.name" :account-id="dialogType !== 'add' ? Number(accountForm.id) : null"
          @busy="credentialBusy = $event" @imported="handleCredentialImported" />
        <!-- 二维码显示区域 -->
        <div v-if="sseConnecting || loginStatus" class="qrcode-container" :class="{ 'browser-mode': loginMode === 'browser' }">
          <div v-if="qrCodeData && !loginStatus" class="qrcode-wrapper">
            <p class="qrcode-tip">{{ loginProgress || '请使用对应平台APP扫码，并在手机上确认' }}</p>
            <img :src="qrCodeData" alt="登录二维码" class="qrcode-image" @error="failLogin('二维码图片加载失败，请重试')" />
          </div>
          <div v-else-if="!qrCodeData && !loginStatus" class="loading-wrapper">
            <el-icon class="is-loading"><Refresh /></el-icon>
            <span>{{ loginProgress || '正在获取登录二维码…' }}</span>
          </div>
          <div v-else-if="loginStatus === '200'" class="success-wrapper">
            <el-icon><CircleCheckFilled /></el-icon>
            <span>添加成功</span>
          </div>
          <div v-else-if="loginStatus === '500'" class="error-wrapper">
            <el-icon><CircleCloseFilled /></el-icon>
            <span>{{ loginError }}</span>
            <el-button v-if="canContinueInBrowser" type="primary" @click="startOfficialLogin">
              打开浏览器完成验证
            </el-button>
          </div>
        </div>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button :disabled="credentialBusy" @click="dialogVisible = false">取消</el-button>
          <el-button 
            v-if="!canContinueInBrowser && !isCredentialImport"
            type="primary" 
            @click="submitAccountForm" 
            :loading="sseConnecting" 
            :disabled="sseConnecting || credentialBusy || loginStatus === '200' || (accountForm.platform === 'YouTube' && dialogType !== 'edit' && !youtubeConfigured)"
          >
            {{ loginButtonLabel }}
          </el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { Refresh, CircleCheckFilled, CircleCloseFilled, Loading } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { accountApi } from '@/api/account'
import { createLoginStream } from '@/utils/loginStream'
import DouyinCredentialImport from '@/components/DouyinCredentialImport.vue'
import YouTubeOAuthSetup from '@/components/YouTubeOAuthSetup.vue'
import { useAccountStore, platformTypes } from '@/stores/account'
import { useAppStore } from '@/stores/app'
import { http, apiBaseUrl } from '@/utils/request'

// 获取账号状态管理
const accountStore = useAccountStore()
// 获取应用状态管理
const appStore = useAppStore()

// 当前激活的标签页
const activeTab = ref('all')
const accountTabs = [{ name: 'all', label: '全部' }, ...[4, 3, 2, 1, 5, 6, 7].map(type => ({ name: String(type), label: platformTypes[type] }))]
const accountsForTab = (label) => label === '全部' ? filteredAccounts.value : filteredAccounts.value.filter(a => a.platform === label)

// 搜索关键词
const searchKeyword = ref('')

// 列表立即显示上次保存的状态；校验进度不再覆盖账号状态。
const fetchAccountsQuick = async () => {
  try {
    await accountStore.loadAccounts()
    appStore.setAccountManagementVisited()
  } catch (error) {
    ElMessage.error(accountStore.loadError || '获取账号列表失败')
  }
}

// 手动刷新时再校验。账号添加成功后已经校验过，无需重复打开浏览器。
const fetchAccounts = async () => {
  if (appStore.isAccountRefreshing) return
  appStore.setAccountRefreshing(true)
  try {
    await accountStore.loadAccounts()
    await accountStore.validateAccounts()
    appStore.setAccountManagementVisited()
    if (accountStore.validationError) ElMessage.warning(accountStore.validationError)
    else ElMessage.success('账号状态已更新')
  } catch (error) {
    ElMessage.warning(accountStore.validationError || accountStore.loadError || '获取账号数据失败')
  } finally {
    appStore.setAccountRefreshing(false)
  }
}

onMounted(fetchAccountsQuick)

// 获取平台标签类型
const getPlatformTagType = (platform) => {
  const typeMap = {
    '快手': 'success',
    '抖音': 'danger',
    '视频号': 'warning',
    '小红书': 'info'
  }
  return typeMap[platform] || 'info'
}

// 判断状态是否可点击（异常状态可点击）
const isStatusClickable = (status) => {
  return status === '异常'; // 只有异常状态可点击，验证中不可点击
}

// 获取状态标签类型
const getStatusTagType = (status) => {
  if (status === '验证中') {
    return 'info'; // 验证中使用灰色
  } else if (status === '正常') {
    return 'success'; // 正常使用绿色
  } else {
    return 'danger'; // 无效使用红色
  }
}

// 处理状态点击事件
const handleStatusClick = (row) => {
  if (isStatusClickable(row.status)) {
    // 触发重新登录流程
    handleReLogin(row)
  }
}

// 过滤后的账号列表
const filteredAccounts = computed(() => {
  if (!searchKeyword.value) return accountStore.accounts
  return accountStore.accounts.filter(account =>
    account.name.includes(searchKeyword.value)
  )
})

// 搜索处理
const handleSearch = () => {
  // 搜索逻辑已通过计算属性实现
}

// 对话框相关
const dialogVisible = ref(false)
const dialogType = ref('add') // 'add' 或 'edit'
const accountFormRef = ref(null)

// 账号表单
const accountForm = reactive({
  id: null,
  name: '',
  platform: '',
  status: '正常'
})

// 表单验证规则
const rules = computed(() => ({
  platform: [{ required: true, message: '请选择平台', trigger: 'change' }],
  name: dialogType.value === 'add' ? [] : [{ required: true, message: '请输入账号名称', trigger: 'blur' }]
}))

// SSE连接状态
const sseConnecting = ref(false)
const qrCodeData = ref('')
const loginStatus = ref('')
const loginError = ref('')
const loginProgress = ref('')
const loginMode = ref('browser')
const loginStage = ref('')
const youtubeConfigured = ref(false)
const loginButtonLabel = computed(() => {
  if (loginStatus.value === '200') return '已成功'
  if (sseConnecting.value) {
    if (['saving', 'verifying'].includes(loginStage.value)) return '正在保存'
    return accountForm.platform === 'YouTube' ? '等待 Google 授权' : loginMode.value === 'browser' ? '等待浏览器登录' : qrCodeData.value ? '等待扫码' : '获取二维码'
  }
  if (dialogType.value === 'edit') return '保存修改'
  if (accountForm.platform === 'YouTube') return 'Google 授权'
  return loginMode.value === 'browser' ? '打开 Edge 登录' : loginStatus.value === '500' ? '重试' : '确认'
})
const credentialBusy = ref(false)
const isCredentialImport = computed(() => ['抖音', 'B站'].includes(accountForm.platform) && loginMode.value === 'import' && ['add', 'import', 'relogin'].includes(dialogType.value))
const loginNeedsVerification = ref(false)
const canContinueInBrowser = computed(() => loginMode.value !== 'browser' && accountForm.platform === '抖音' && loginStatus.value === '500' && loginNeedsVerification.value)

// 添加账号
const handleAddAccount = () => {
  dialogType.value = 'add'
  Object.assign(accountForm, {
    id: null,
    name: '',
    platform: platformTypes[Number(activeTab.value)] || '',
    status: '正常'
  })
  // 重置SSE状态
  sseConnecting.value = false
  qrCodeData.value = ''
  loginStatus.value = ''
  resetLoginMethod()
  loginMode.value = 'browser'
  loginNeedsVerification.value = false
  dialogVisible.value = true
}

// 编辑账号
const handleEdit = (row) => {
  dialogType.value = 'edit'
  Object.assign(accountForm, {
    id: row.id,
    name: row.name,
    platform: row.platform,
    status: row.status
  })
  dialogVisible.value = true
}

// 删除账号
const handleDelete = (row) => {
  ElMessageBox.confirm(
    `确定要删除账号 ${row.name} 吗？`,
    '警告',
    {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    }
  )
    .then(async () => {
      try {
        // 调用API删除账号
        const response = await accountApi.deleteAccount(row.id)

        if (response.code === 200) {
          // 从状态管理中删除账号
          accountStore.deleteAccount(row.id)
          ElMessage({
            type: 'success',
            message: '删除成功',
          })
        } else {
          ElMessage.error(response.msg || '删除失败')
        }
      } catch (error) {
        console.error('删除账号失败:', error)
        ElMessage.error('删除账号失败')
      }
    })
    .catch(() => {
      // 取消删除
    })
}

// 下载Cookie文件
const handleDownloadCookie = (row) => {
  // 从后端获取Cookie文件
  const downloadUrl = `${apiBaseUrl}/downloadCookie?filePath=${encodeURIComponent(row.filePath)}`

  // 创建一个隐藏的链接来触发下载
  const link = document.createElement('a')
  link.href = downloadUrl
  link.download = `${row.name}_cookie.json`
  link.target = '_blank'
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

// 上传Cookie文件
const handleUploadCookie = (row) => {
  if (['抖音', 'B站'].includes(row.platform)) {
    resetLoginMethod()
    dialogType.value = 'import'
    Object.assign(accountForm, { id: row.id, name: row.name, platform: row.platform, status: row.status })
    loginMode.value = 'import'
    dialogVisible.value = true
    return
  }
  // 创建一个隐藏的文件输入框
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json'
  input.style.display = 'none'
  document.body.appendChild(input)

  input.onchange = async (event) => {
    const file = event.target.files[0]
    if (!file) return

    // 检查文件类型
    if (!file.name.endsWith('.json')) {
      ElMessage.error('请选择JSON格式的Cookie文件')
      document.body.removeChild(input)
      return
    }

    try {
      // 创建FormData对象
      const formData = new FormData()
      formData.append('file', file)
      formData.append('id', row.id)
      formData.append('platform', row.platform)

      // 使用统一的http封装发送上传请求
      const result = await http.upload('/uploadCookie', formData)

      ElMessage.success('Cookie文件上传成功')
      // 刷新账号列表以显示更新
      fetchAccounts()
    } catch (error) {
      ElMessage.error('Cookie文件上传失败')
    } finally {
      document.body.removeChild(input)
    }
  }

  input.click()
}

// 重新登录账号
const handleReLogin = (row) => {
  resetLoginMethod()
  dialogType.value = 'relogin'
  Object.assign(accountForm, { id: row.id, name: row.name, platform: row.platform, status: row.status })
  loginMode.value = 'browser'
  dialogVisible.value = true
}

// 获取默认头像
// 登录连接和成功提示定时器只属于当前对话框。
let loginStream = null
let successTimer = null

const closeSSEConnection = () => {
  loginStream?.close()
  loginStream = null
  if (successTimer !== null) {
    clearTimeout(successTimer)
    successTimer = null
  }
}

const resetLoginMethod = () => {
  closeSSEConnection()
  sseConnecting.value = false
  qrCodeData.value = ''
  loginStatus.value = ''
  loginError.value = ''
  loginProgress.value = ''
  loginNeedsVerification.value = false
}
const handleLoginPlatformChange = () => {
  resetLoginMethod()
  loginMode.value = 'browser'
}
const handleCredentialImported = () => {
  loginStatus.value = '200'
  credentialBusy.value = false
  successTimer = setTimeout(() => {
    successTimer = null
    dialogVisible.value = false
    ElMessage.success(`${accountForm.platform}账号已验证并保存`)
    fetchAccountsQuick()
  }, 800)
}

const failLogin = (message, details) => {
  closeSSEConnection()
  loginStatus.value = '500'
  loginError.value = message
  loginNeedsVerification.value = details?.status === 'verification_required'
  sseConnecting.value = false
}

watch(dialogVisible, (visible) => {
  if (!visible) {
    closeSSEConnection()
    sseConnecting.value = false
    qrCodeData.value = ''
    loginStatus.value = ''
    loginError.value = ''
    loginProgress.value = ''
    credentialBusy.value = false
    loginNeedsVerification.value = false
  }
})

const connectSSE = (platform, name, mode = 'browser') => {
  closeSSEConnection()
  sseConnecting.value = true
  qrCodeData.value = ''
  loginStatus.value = ''
  loginError.value = ''
  loginMode.value = mode
  loginNeedsVerification.value = false
  loginStage.value = ''
  loginProgress.value = mode === 'browser' ? '正在打开 Edge 登录窗口…' : '正在获取登录二维码…'

  const type = { '小红书': '1', '视频号': '2', '抖音': '3', '快手': '4', 'B站': '5', 'YouTube': '6', '今日头条': '7' }[platform]
  if (!type) {
    failLogin('平台类型无效，请重新选择')
    return
  }
  const replacement = dialogType.value === 'relogin' ? `&account_id=${accountForm.id}` : ''
  const url = `${apiBaseUrl}/login?type=${type}&id=${encodeURIComponent(name.trim())}&mode=${mode}${replacement}`
  loginStream = createLoginStream(url, {
    onQr: (image) => {
      qrCodeData.value = image
      loginProgress.value = platform === '视频号'
        ? '请使用微信扫一扫，扫码后在手机上确认'
        : '请使用对应平台APP扫码，并在手机上确认'
    },
    onStatus: ({ message, stage }) => { loginProgress.value = message; loginStage.value = stage || '' },
    onError: failLogin,
    onSuccess: () => {
      loginStream = null
      loginStatus.value = '200'
      sseConnecting.value = false
      successTimer = setTimeout(() => {
        successTimer = null
        dialogVisible.value = false
        ElMessage.success('账号登录成功')
        fetchAccountsQuick()
      }, 800)
    }
  }, { mode })
}

const startOfficialLogin = () => {
  accountFormRef.value.validate((valid) => {
    if (valid) connectSSE(accountForm.platform, accountForm.name, 'browser')
  })
}

// 提交账号表单
const submitAccountForm = () => {
  accountFormRef.value.validate(async (valid) => {
    if (valid) {
      if (['add', 'relogin'].includes(dialogType.value)) {
        // 建立SSE连接
        connectSSE(accountForm.platform, accountForm.name, loginMode.value)
      } else {
        // 编辑账号逻辑
        try {
          // 将平台名称转换为类型数字
          const platformTypeMap = {
            '小红书': 1,
            '视频号': 2,
            '抖音': 3,
            '快手': 4,
            'B站': 5,
            'YouTube': 6,
            '今日头条': 7
          };
          const type = platformTypeMap[accountForm.platform] || 1;

          const res = await accountApi.updateAccount({
            id: accountForm.id,
            type: type,
            userName: accountForm.name
          })
          if (res.code === 200) {
            // 更新状态管理中的账号
            const updatedAccount = {
              id: accountForm.id,
              name: accountForm.name,
              platform: accountForm.platform,
              status: accountForm.status // Keep the existing status
            };
            accountStore.updateAccount(accountForm.id, updatedAccount)
            ElMessage.success('更新成功')
            dialogVisible.value = false
            // 刷新账号列表
            fetchAccounts()
          } else {
            ElMessage.error(res.msg || '更新账号失败')
          }
        } catch (error) {
          console.error('更新账号失败:', error)
          ElMessage.error('更新账号失败')
        }
      }
    } else {
      return false
    }
  })
}

const handleAccountCommand = (command, row) => {
  if (command === 'edit') handleEdit(row)
  else if (command === 'download') handleDownloadCookie(row)
  else if (command === 'upload') handleUploadCookie(row)
  else if (command === 'delete') handleDelete(row)
}

// 组件卸载前关闭SSE连接
onBeforeUnmount(() => {
  closeSSEConnection()
})
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.browser-login-intro { font-size: 14px; line-height: 1.7; padding: 0 4px; }
.browser-login-note { font-size: 12px; color: var(--el-text-color-secondary); }
.mobile-account-list { display: none; }

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.account-management {
  .page-header {
    margin-bottom: 20px;
    
    h1 {
      font-size: clamp(26px, 2.4vw, 32px);
      color: var(--ui-text);
      margin: 0;
    }
  }
  
  .account-tabs {
    background: var(--ui-surface);
    border: 1px solid var(--ui-border);
    border-radius: 16px;
    box-shadow: var(--ui-raised);
    
    .account-tabs-nav {
      padding: 20px;
    }
  }
  
  .account-list-container {
    .account-search {
      display: flex;
      justify-content: space-between;
      margin-bottom: 20px;
      
      .el-input {
        width: 300px;
      }
      
      .action-buttons {
        display: flex;
        gap: 10px;
        
        .el-icon.is-loading {
          animation: rotate 1s linear infinite;
        }
      }
    }
    
    .account-list {
      margin-bottom: 20px;
    }
    
    .empty-data {
      padding: 40px 0;
    }
  }
  
  // 二维码容器样式
  .clickable-status {
    cursor: pointer;
    transition: all 0.3s;

    &:hover {
      transform: scale(1.05);
      box-shadow: 0 0 8px rgba(0, 0, 0, 0.15);
    }
  }

  .qrcode-container {
    margin-top: 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 250px;

    &.browser-mode { min-height: 130px; }
    
    .qrcode-wrapper {
      text-align: center;
      
      .qrcode-tip {
        margin-bottom: 15px;
        color: var(--ui-muted);
      }
      
      .qrcode-image {
        max-width: 200px;
        max-height: 200px;
        border: 1px solid #ebeef5;
        background-color: black;
      }
    }
    
    .loading-wrapper, .success-wrapper, .error-wrapper {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 10px;
      
      .el-icon {
        font-size: 48px;
        
        &.is-loading {
          animation: rotate 1s linear infinite;
        }
      }
      
      span {
        font-size: 16px;
      }
    }
    
    .success-wrapper .el-icon {
      color: #67c23a;
    }
    
    .error-wrapper .el-icon {
      color: #f56c6c;
    }

    .loading-wrapper span, .error-wrapper span {
      text-align: center;
      line-height: 1.5;
    }
  }

  .dialog-footer {
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 8px;

    .el-button + .el-button {
      margin-left: 0;
    }
  }
}
@media (max-width: 1050px) {
  .account-management .account-tabs .account-tabs-nav { padding: 14px; }
  .account-management .account-list-container .account-search { align-items: stretch; flex-direction: column; gap: 12px; }
  .account-management .account-list-container .account-search .el-input { width: 100%; }
  .account-management .account-list-container .account-search .action-buttons { justify-content: flex-end; }
  .desktop-account-table { display: none; }
  .mobile-account-list { display: grid; gap: 12px; }
  .mobile-account-card { min-width: 0; padding: 14px; border: 1px solid var(--ui-border); border-radius: 13px; box-shadow: var(--ui-inset); }
  .mobile-account-head { display: flex; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
  .mobile-account-head strong { overflow-wrap: anywhere; }
  .mobile-account-card p { margin: 8px 0 14px; color: var(--ui-muted); }
  .mobile-account-actions { display: flex; gap: 8px; flex-wrap: wrap; }
  .mobile-account-actions .el-button { min-height: 40px; margin: 0; }
}

.account-management { max-width: 1440px; margin: auto; padding: 8px 4px 42px; }
.eyebrow { color: var(--el-color-primary); font-size: 12px; letter-spacing: .08em; font-weight: 600; margin-bottom: 3px; }
.page-description { color: var(--ui-muted); font-size: 14px; margin-top: 6px; line-height: 1.6; }
.account-avatar { background: var(--ui-soft); color: var(--el-color-primary); border: 1px solid var(--ui-border); border-radius: 12px; font-size: 14px; font-weight: 600; }
.account-tabs :deep(.el-tabs__item) { height: 46px; font-weight: 500; }
.account-tabs :deep(.el-tabs__nav-wrap::after) { height: 1px; background: var(--ui-border); }
.account-tabs :deep(.el-tabs__header) { margin-bottom: 20px; }
.mobile-account-card { background: var(--ui-surface); }

</style>

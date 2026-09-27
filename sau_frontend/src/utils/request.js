import axios from 'axios'
import { ElMessage } from 'element-plus'
import { normalizeApiError } from './requestErrors.js'

// 桌面版可以使用独立端口，登录流和普通请求必须连接同一个服务。
export const apiBaseUrl = import.meta.env?.VITE_API_BASE_URL || (import.meta.env?.PROD ? window.location.origin : 'http://localhost:5409')

// 创建axios实例
const request = axios.create({
  baseURL: apiBaseUrl,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    // 可以在这里添加token等认证信息
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// Pages with inline feedback opt out of global toasts to avoid duplicate errors.
function rejectApiError(error, config) {
  const normalized = normalizeApiError(error)
  if (config?.silentError !== true) ElMessage.error(normalized.message)
  return Promise.reject(normalized)
}

request.interceptors.response.use(
  response => {
    const { data } = response
    if (data?.code === 200 || data?.success === true) return data
    const error = new Error()
    error.response = response
    error.config = response.config
    return rejectApiError(error, response.config)
  },
  error => rejectApiError(error, error.config)
)

// 封装常用的请求方法
export const http = {
  get(url, params, config = {}) {
    return request.get(url, { ...config, params })
  },
  
  post(url, data, config = {}) {
    return request.post(url, data, config)
  },
  
  put(url, data, config = {}) {
    return request.put(url, data, config)
  },
  
  delete(url, params) {
    return request.delete(url, { params })
  },
  
  upload(url, formData, onUploadProgress) {
    return request.post(url, formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      },
      onUploadProgress
    })
  }
}

export default request

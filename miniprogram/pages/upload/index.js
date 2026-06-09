const config = require('../../utils/config')

const STATE_TEXT = {
  idle: '等待选择文件',
  choosing: '正在打开微信聊天文件选择器',
  ready: '已选择文件，准备上传',
  uploading: '正在上传',
  success: '上传完成',
  error: '上传失败',
}

Page({
  data: {
    sessionId: '',
    ticket: '',
    apiBaseUrl: '',
    contextText: '',
    configError: '',
    isChoosing: false,
    isUploading: false,
    status: 'idle',
    statusText: STATE_TEXT.idle,
    statusClass: 'is-idle',
    errorMessage: '',
    uploadItems: [],
    canChoose: false,
    primaryButtonText: '从微信聊天选择资料',
  },

  onLoad(options) {
    const sessionId = safeDecode(options.session_id || '')
    const ticket = safeDecode(options.ticket || '')
    const apiBaseUrl = trimTrailingSlash(safeDecode(options.api_base_url || config.API_BASE_URL))
    const contextText = safeDecode(options.context_text || '')

    const missing = []
    if (!sessionId) missing.push('session_id')
    if (!ticket) missing.push('ticket')
    if (!apiBaseUrl) missing.push('api_base_url')

    const configError = missing.length ? `请从 /wx 页面进入上传页，当前缺少：${missing.join(', ')}` : ''

    this.setData({
      sessionId,
      ticket,
      apiBaseUrl,
      contextText,
      configError,
      canChoose: !configError,
      primaryButtonText: configError ? '参数不完整，无法上传' : '从微信聊天选择资料',
    })
  },

  handleContextInput(event) {
    this.setData({ contextText: event.detail.value })
  },

  chooseAndUpload() {
    if (!this.data.canChoose || this.data.isChoosing || this.data.isUploading) {
      return
    }

    this.setStatus('choosing')
    this.setData({ isChoosing: true, errorMessage: '', uploadItems: [] })

    wx.chooseMessageFile({
      count: config.MAX_UPLOAD_COUNT,
      type: 'all',
      success: (res) => {
        const files = Array.isArray(res.tempFiles) ? res.tempFiles : []
        const acceptedFiles = files.filter(isAcceptedFile)
        const rejectedCount = files.length - acceptedFiles.length

        if (!acceptedFiles.length) {
          this.setError(rejectedCount ? '所选文件类型暂不支持，请选择 PDF、Word 或图片资料。' : '没有选择文件。')
          return
        }

        const uploadItems = acceptedFiles.map((file, index) => createUploadItem(file, index))
        const warning = rejectedCount ? `已跳过 ${rejectedCount} 个暂不支持的文件。` : ''

        this.setData({ uploadItems, errorMessage: warning })
        this.setStatus('ready')
        this.uploadFilesSequentially(acceptedFiles)
      },
      fail: (error) => {
        const message = isCancelError(error) ? '已取消选择文件。' : getWxErrorMessage(error, '选择文件失败。')
        this.setError(message)
      },
      complete: () => {
        this.setData({ isChoosing: false })
      },
    })
  },

  async uploadFilesSequentially(files) {
    this.setStatus('uploading')
    this.setData({ isUploading: true, primaryButtonText: '上传中...' })

    let successCount = 0
    let failureCount = 0

    for (let index = 0; index < files.length; index += 1) {
      try {
        await this.uploadOneFile(files[index], index)
        successCount += 1
      } catch (error) {
        failureCount += 1
        this.updateUploadItem(index, {
          state: 'error',
          stateText: '失败',
          progressColor: '#dc2626',
          error: error.message || '上传失败',
        })
      }
    }

    this.setData({ isUploading: false, primaryButtonText: '继续选择资料' })

    if (successCount > 0 && failureCount === 0) {
      this.setStatus('success')
      this.setData({ errorMessage: `已成功上传 ${successCount} 个文件。` })
      return
    }

    if (successCount > 0) {
      this.setStatus('error')
      this.setData({ errorMessage: `已上传 ${successCount} 个文件，${failureCount} 个文件失败。` })
      return
    }

    this.setError('所有文件上传失败，请稍后重试。')
  },

  uploadOneFile(file, index) {
    const uploadUrl = buildUploadUrl(this.data.apiBaseUrl, this.data.ticket)

    this.updateUploadItem(index, {
      state: 'uploading',
      stateText: '上传中',
      progress: 0,
      progressColor: '#2563eb',
      error: '',
    })

    return new Promise((resolve, reject) => {
      const task = wx.uploadFile({
        url: uploadUrl,
        filePath: file.path,
        name: 'file',
        header: {
          Accept: 'application/json',
          'X-DS160-Client': 'wechat-miniprogram',
        },
        formData: {
          session_id: this.data.sessionId,
          context_text: this.data.contextText || '',
          source: 'wechat_message_file',
          original_name: file.name || '',
        },
        success: (res) => {
          try {
            const payload = parseUploadResponse(res)
            this.updateUploadItem(index, {
              state: 'success',
              stateText: '已完成',
              progress: 100,
              progressColor: '#16a34a',
              response: payload,
              error: '',
            })
            resolve(payload)
          } catch (error) {
            reject(error)
          }
        },
        fail: (error) => {
          reject(new Error(getWxErrorMessage(error, '网络上传失败。')))
        },
      })

      if (task && typeof task.onProgressUpdate === 'function') {
        task.onProgressUpdate((progressEvent) => {
          this.updateUploadItem(index, {
            progress: Math.max(0, Math.min(100, progressEvent.progress || 0)),
          })
        })
      }
    })
  },

  updateUploadItem(index, patch) {
    const uploadItems = this.data.uploadItems.slice()
    if (!uploadItems[index]) return
    uploadItems[index] = Object.assign({}, uploadItems[index], patch)
    this.setData({ uploadItems })
  },

  setStatus(status) {
    this.setData({
      status,
      statusText: STATE_TEXT[status] || STATE_TEXT.idle,
      statusClass: `is-${status}`,
      errorMessage: status === 'error' ? this.data.errorMessage : this.data.errorMessage,
    })
  },

  setError(message) {
    this.setData({
      status: 'error',
      statusText: STATE_TEXT.error,
      statusClass: 'is-error',
      errorMessage: message,
      isChoosing: false,
      isUploading: false,
      primaryButtonText: this.data.configError ? '参数不完整，无法上传' : '重新选择资料',
    })
  },

  goBack() {
    wx.navigateBack({
      delta: 1,
      fail: () => {
        wx.redirectTo({ url: '/pages/webview/index' })
      },
    })
  },
})

function safeDecode(value) {
  if (!value) return ''
  try {
    return decodeURIComponent(value)
  } catch (error) {
    console.warn('failed to decode query value', error)
    return value
  }
}

function trimTrailingSlash(value) {
  return String(value || '').replace(/\/+$/, '')
}

function buildUploadUrl(apiBaseUrl, ticket) {
  return `${trimTrailingSlash(apiBaseUrl)}/v1/wx/upload-tickets/${encodeURIComponent(ticket)}/files`
}

function createUploadItem(file, index) {
  return {
    id: `${Date.now()}-${index}`,
    name: file.name || getFileNameFromPath(file.path) || `资料 ${index + 1}`,
    sizeText: formatBytes(file.size),
    progress: 0,
    progressColor: '#2563eb',
    state: 'pending',
    stateText: '等待上传',
    error: '',
    response: null,
  }
}

function isAcceptedFile(file) {
  const name = file.name || file.path || ''
  const ext = getExtension(name)
  if (!ext) return true
  return config.ACCEPTED_EXTENSIONS.indexOf(ext) !== -1
}

function getExtension(name) {
  const normalized = String(name || '').split('?')[0].split('#')[0]
  const dotIndex = normalized.lastIndexOf('.')
  if (dotIndex < 0 || dotIndex === normalized.length - 1) return ''
  return normalized.slice(dotIndex + 1).toLowerCase()
}

function getFileNameFromPath(path) {
  if (!path) return ''
  const normalized = String(path).split('?')[0].split('#')[0]
  return normalized.slice(normalized.lastIndexOf('/') + 1)
}

function formatBytes(size) {
  const bytes = Number(size || 0)
  if (!bytes) return '未知大小'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function parseUploadResponse(res) {
  const statusCode = Number(res.statusCode || 0)
  let payload = null

  if (typeof res.data === 'string' && res.data.length) {
    try {
      payload = JSON.parse(res.data)
    } catch (error) {
      throw new Error(`服务器返回不是有效 JSON：${res.data.slice(0, 120)}`)
    }
  }

  if (statusCode < 200 || statusCode >= 300) {
    const serverMessage = payload && (payload.detail || payload.message || payload.error)
    throw new Error(serverMessage || `服务器返回 HTTP ${statusCode}`)
  }

  return payload || {}
}

function getWxErrorMessage(error, fallback) {
  if (!error) return fallback
  return error.errMsg || error.message || fallback
}

function isCancelError(error) {
  const message = getWxErrorMessage(error, '')
  return /cancel|取消/i.test(message)
}

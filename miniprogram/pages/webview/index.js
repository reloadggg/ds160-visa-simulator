const config = require('../../utils/config')

Page({
  data: {
    webviewUrl: config.WEBVIEW_URL,
    loadError: false,
  },

  onLoad(options) {
    const candidateUrl = options && options.url ? safeDecode(options.url) : config.WEBVIEW_URL
    this.setData({
      webviewUrl: candidateUrl || config.WEBVIEW_URL,
      loadError: false,
    })
  },

  handleWebviewLoad() {
    this.setData({ loadError: false })
  },

  handleWebviewError(event) {
    console.warn('web-view load error', event && event.detail)
    this.setData({ loadError: true })
  },

  retry() {
    this.setData({ loadError: false })
  },
})

function safeDecode(value) {
  try {
    return decodeURIComponent(value)
  } catch (error) {
    console.warn('failed to decode webview url', error)
    return value
  }
}

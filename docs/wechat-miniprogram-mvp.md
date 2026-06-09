# WeChat Mini Program lightweight shell

This folder is a source-only WeChat Mini Program shell for the DS-160 lightweight web-view entry. It does not depend on npm or a Mini Program SDK package; open `miniprogram/` directly with WeChat Developer Tools.

The current lightweight entry intentionally uses the existing access-key auth boundary. It does **not** implement `wx.login`, OpenID binding, or a separate Mini Program account system. A user can enter `/wx` with a normal access key, open an admin-generated share link, or copy their current Key share link from workbench Settings after a Key login, then use `https://YOUR_DOMAIN/#ds160_access_key=<access-key-secret>` to click enable/use before entering the workbench. After a shared-key login succeeds, the H5 page clears the key from the address bar.

## Backend release switch

The `/wx` entry is controlled by the admin/backend setting `wx_entry_enabled`.

- Default: `false`.
- Public contract: `GET /v1/app-config` returns `wx_entry_enabled`.
- Admin contract: `GET /v1/admin/settings` and `PATCH /v1/admin/settings` include `wx_entry_enabled`.
- Admin UI: `/admin` → **功能开关** → **微信端入口（默认关闭）**.

When the flag is `false`, `/wx` shows a closed-state notice (`微信端内测中`) and a **返回首页** button. It does not render or enable the WeChat workbench until the flag is enabled.

## Files

```text
miniprogram/
  project.config.json
  app.json
  app.js
  app.wxss
  utils/config.js
  pages/webview/index.*
  pages/upload/index.*
```

- `pages/webview/index` opens the configured H5 URL through `<web-view>`.
- `pages/upload/index` reads `session_id`, `ticket`, and `api_base_url` from the route query, calls `wx.chooseMessageFile`, and uploads the selected files with `wx.uploadFile` to `/v1/wx/upload-tickets/{ticket}/files` using multipart field name `file`.

## Required configuration

Edit `miniprogram/utils/config.js` before phone validation:

```js
module.exports = {
  WEBVIEW_URL: 'https://YOUR_DOMAIN/wx',
  API_BASE_URL: 'https://YOUR_DOMAIN',
}
```

These values must point to HTTPS domains that are configured in the WeChat Mini Program console:

1. **Business domain** for `web-view`: the host serving `WEBVIEW_URL`.
2. **request legal domain** if the native shell later uses `wx.request`.
3. **uploadFile legal domain** for `API_BASE_URL`.
4. Optional **downloadFile legal domain** if later file preview/download is added.

Official WeChat constraints still apply: non-personal Mini Program account for `web-view`, valid HTTPS certificate, configured legal domains, and production/trial phone validation through WeChat Developer Tools or the Mini Program console.

## Open with WeChat Developer Tools

1. Open WeChat Developer Tools.
2. Import project and select `ds160-visa-simulator/miniprogram/`.
3. Replace `touristappid` in `project.config.json` with the real AppID, or keep tourist mode for limited local UI inspection.
4. Edit `utils/config.js` with the deployed `/wx` URL and API base URL.
5. In the Mini Program console, configure the business domain and uploadFile legal domain.
6. Build/preview the Mini Program and open `pages/webview/index`.

## Native upload page contract

The H5 `/wx` page should navigate to the native upload page with encoded query parameters:

```text
/pages/upload/index?session_id=<session_id>&ticket=<upload_ticket>&api_base_url=<encoded_api_base_url>&context_text=<optional_encoded_text>
```

The native page uploads each selected file to:

```text
${api_base_url}/v1/wx/upload-tickets/${ticket}/files
```

`wx.uploadFile` options used by the page:

```js
{
  url,
  filePath,
  name: 'file',
  formData: {
    session_id,
    context_text,
    source: 'wechat_message_file',
    original_name,
  },
}
```

The page parses `res.data` as JSON, checks `res.statusCode`, displays progress/success/error state, and provides a return button.

## Backend upload ticket API

The H5 `/wx` page creates a short-lived upload ticket after the user is authenticated and has selected a session:

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/sessions/{session_id}/upload-ticket` | Session access | Create a short-lived ticket for native upload |
| `GET` | `/v1/wx/upload-tickets/{ticket}` | Ticket | Read ticket status and upload results |
| `POST` | `/v1/wx/upload-tickets/{ticket}/files` | Ticket | Upload one WeChat-selected file as multipart field `file` |

Default ticket behavior:

- TTL: 300 seconds.
- File limit: 5 files by default; service caps custom values at 10.
- Storage: the database stores `sha256(ticket)`, not the raw ticket.
- Scope: the ticket is bound to one `session_id`; if the upload form also sends `session_id`, a mismatch returns `403`.
- Logging caveat: the raw ticket appears in URL/path while the Mini Program uploads, so Nginx or access logs may record it. Treat it as a short-lived bearer credential.

Status payload:

```json
{
  "ticket": "wxup_<short-lived-token>",
  "session_id": "sess_abc123",
  "expires_at": "2026-06-09T08:05:00Z",
  "max_files": 5,
  "uploaded_count": 1,
  "remaining_files": 4,
  "status": "active",
  "upload_results": [
    {
      "document_id": "doc_abc123",
      "file_name": "i20.pdf",
      "mime_type": "application/pdf",
      "size": 12345,
      "uploaded_at": "2026-06-09T08:01:00Z"
    }
  ]
}
```

Upload success returns HTTP `202` and includes both the ticket status fields and an `upload` object matching the normal material-upload contract. The uploaded file then enters the existing material-understanding queue.

Error matrix:

| Status | Typical cause |
| --- | --- |
| `403` | Submitted `session_id` does not match the ticket's session |
| `404` | Ticket or bound session no longer exists |
| `409` | Ticket completed/inactive/file limit exceeded, or the interview session is already terminal |
| `410` | Ticket expired |
| `413` | File exceeds backend upload size limit |
| `415` | File type unsupported by the backend file service |

## Manual smoke test

1. Open `pages/webview/index` in WeChat Developer Tools and confirm it loads `WEBVIEW_URL`.
2. Log in from `/wx` with an access key, or open a share link and click enable/use; no display name is required during login.
3. From `/wx`, create a backend upload ticket and call `wx.miniProgram.navigateTo` with the upload page query above.
4. On `pages/upload/index`, enter optional context text.
5. Tap **从微信聊天选择资料** and select a PDF, Word document, or image from a WeChat chat.
6. Confirm progress reaches 100% and the page shows success.
7. Tap **返回** and let `/wx` refresh ticket status/material/report state.

## Production checklist

- `WEBVIEW_URL` points to the deployed HTTPS `/wx` route.
- `API_BASE_URL` points to the same HTTPS origin unless a separate API origin is intentionally configured.
- Mini Program console has the web-view business domain, request legal domain, and uploadFile legal domain configured.
- Reverse proxy forwards `/wx` to the web container and `/api/v1/wx/upload-tickets/...` plus `/api/v1/sessions/.../upload-ticket` to FastAPI.
- Reverse proxy upload size is aligned with the backend file upload limit.
- Access-key share links use hash (`/#ds160_access_key=...`) rather than query parameters.
- Operators understand that display name is set in the workbench profile/settings surface, not during login.

## Known lightweight-entry limits

- This is not a full native Mini Program rewrite.
- It does not implement `wx.login` or OpenID binding.
- It does not replace the existing desktop web app.
- Phone-openable validation can still be blocked by AppID/domain/ICP/business-domain setup even when the source code is correct.

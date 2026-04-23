# Upload Flow Thinking Guide

> **Purpose**: Prevent upload UX logic from leaking into the frontend.

## When To Use
- A chat message can contain both text and attachments.
- The UI wants to ask the user to classify an uploaded file.
- The backend already performs multimodal extraction or document assessment.

## Checklist
- [ ] Does the UI forward the raw chat text unchanged to the backend?
- [ ] Is document classification owned by one backend boundary only?
- [ ] If a hint exists, is it treated as a weak prior rather than a required pre-step?
- [ ] Are upload feedback messages aligned with backend `main_flow_feedback`, not frontend guesses?

## Pointer
- Detailed contract: [backend/file-upload-contracts.md](../backend/file-upload-contracts.md)

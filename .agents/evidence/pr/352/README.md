# PR 352 Evidence

This evidence covers the Chat Completions `choices[].delta.reasoning` /
`choices[].delta.reasoning_details` viewer fix and the requested real New API
GPT check.

- `352-before-viewer.png`: reporter-compatible `delta.reasoning` stream before
  the fix; the viewer only renders the visible answer.
- `352-after-viewer.png`: the same stream after the fix; the viewer renders a
  thinking block.
- `real-gpt-5.5-chat-completions-viewer.png`: real New API `gpt-5.5`
  `/v1/chat/completions` stream rendered in the viewer.

The real GPT stream returned visible `delta.content` and usage with
`completion_tokens_details.reasoning_tokens`, but did not emit
`choices[].delta.reasoning`, `reasoning_details`, or `reasoning_content`. The
fix is therefore verified with the reporter-compatible OpenAI-compatible
provider field shape, and the real GPT capture validates the live gateway/viewer
path.

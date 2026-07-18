# Issue 392 cURL upstream evidence

This evidence comes from a real OpenCode 1.16.0 forward-proxy run against the
configured OpenCode provider. The isolated trace session is
`820a25b9-e9b6-49d9-b70e-c3f1cf6ee776` and contains three captured records.

The exported viewer records `https://opencode.ai` as `upstream_base_url` for
the model request at `/zen/v1/chat/completions`. Invoking the viewer's cURL
copy action produced:

```text
curl -X POST 'https://opencode.ai/zen/v1/chat/completions' \
```

The screenshot shows the real response marker `ISSUE392_OK` together with the
captured upstream origin and request path. Authentication headers remain
redacted in traces and copied cURL commands by design.

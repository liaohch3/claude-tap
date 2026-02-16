# VHS Demo: claude-tap --tap-live

Output demo-terminal.gif
Output demo-terminal.mp4

Set Shell "bash"
Set FontSize 16
Set Width 1000
Set Height 600
Set Theme "Dracula"
Set Padding 20

# Title
Type "# claude-tap --tap-live Demo"
Enter
Sleep 1s

# Run command
Type "claude-tap --tap-live --tap-no-launch --tap-live-port 8080"
Sleep 500ms
Enter
Sleep 2s

# Fake output
Hide
Type "echo '🔍 Trace proxy listening on http://127.0.0.1:45678'"
Enter
Type "echo '🌐 Live viewer: http://127.0.0.1:8080'"
Enter
Type "echo '📁 Trace file: ./.traces/trace_20260216.jsonl'"
Enter
Show
Sleep 2s

# Curl command
Type "# Sending API request to proxy..."
Enter
Sleep 1s

Type "curl -X POST http://127.0.0.1:45678/v1/messages -d '...'"
Sleep 500ms
Enter
Sleep 2s

# Output
Hide
Type "echo '[Turn 1] → POST /v1/messages (model=claude-3-opus)'"
Enter
Type "echo '[Turn 1] ← 200 stream done (1234ms, in=18 out=15)'"
Enter
Show
Sleep 2s

Type "# Check browser - request appears in real-time!"
Enter
Sleep 3s

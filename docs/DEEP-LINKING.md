# Quarterdeck Deep Linking

`quarterdeck://` is a registered macOS URL scheme. Any script, tool, or app
that can open a URL can spawn a Quarterdeck session — no terminal required.

---

## URL format

```
quarterdeck://intake?template=NAME&var1=VALUE&var2=VALUE
quarterdeck://intake?task=TEXT&cwd=/path/to/project
```

Both routes call `POST /api/intake` internally.

### Parameters

| Parameter  | Description |
|---|---|
| `template` | Template name or id (from Settings → Templates). If omitted, `task` is required. |
| `task`     | Raw task string. Used directly when no template is given, or ignored when a template handles it. |
| `cwd`      | Working directory for the new session. Overrides the template's stored cwd. |
| `agent`    | Agent name (optional). |
| `model`    | Model name (optional). |
| `effort`   | Effort level: `low`, `medium`, `high`, `max` (optional). |
| Any other key | Treated as a template variable and substituted into `{{key}}` slots. |

### Example — direct task

```bash
open "quarterdeck://intake?task=Run+the+test+suite+and+report+failures&cwd=/Users/me/my-project"
```

### Example — template with a variable

Assume you have a template called `"Process transcript"` with task:

```
Summarise this meeting transcript and extract action items:

{{text}}
```

Call it with:

```bash
TRANSCRIPT=$(cat meeting.txt | python3 -c "import sys,urllib.parse; print(urllib.parse.quote(sys.stdin.read()))")
open "quarterdeck://intake?template=Process+transcript&text=${TRANSCRIPT}"
```

---

## Calling from a transcription tool

The typical pattern for a tool that produces a transcript (Whisper, yt-transcript,
meeting recorder, etc.) is:

```bash
#!/bin/bash
# transcribe-and-send.sh
# Usage: ./transcribe-and-send.sh recording.mp3 [template_name]

AUDIO="$1"
TEMPLATE="${2:-Process transcript}"

# 1. Produce the transcript (replace with your actual tool)
TRANSCRIPT=$(whisper "$AUDIO" --output_format txt --output_dir /tmp 2>/dev/null \
  && cat "/tmp/$(basename "$AUDIO" .mp3).txt")

# 2. URL-encode it
ENCODED=$(python3 -c "import sys,urllib.parse; print(urllib.parse.quote(sys.stdin.read()))" <<< "$TRANSCRIPT")

# 3. Open Quarterdeck with the template + encoded text
open "quarterdeck://intake?template=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote('${TEMPLATE}'))")&text=${ENCODED}"
```

### Python version (cleaner encoding)

```python
#!/usr/bin/env python3
"""Send a transcript to Quarterdeck via deep link."""
import subprocess
import urllib.parse
from pathlib import Path

def send_to_quarterdeck(text: str, template: str = "Process transcript", cwd: str = "") -> None:
    params = {
        "template": template,
        "text": text,
    }
    if cwd:
        params["cwd"] = cwd
    url = "quarterdeck://intake?" + urllib.parse.urlencode(params)
    subprocess.run(["open", url], check=True)

if __name__ == "__main__":
    import sys
    transcript = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    template = sys.argv[2] if len(sys.argv) > 2 else "Process transcript"
    send_to_quarterdeck(transcript, template)
    print(f"Sent to Quarterdeck: {len(transcript)} chars via template '{template}'")
```

Usage:

```bash
python3 send_to_quarterdeck.py transcript.txt "Process transcript"
# or pipe it:
cat transcript.txt | python3 send_to_quarterdeck.py
```

---

## Setting up the template

Before calling the URL, create the template in Quarterdeck:

1. Open a session that has the context you want (e.g. a session where you already
   processed one transcript and got good results).
2. In the transcript, hover over the last user turn before the good response and
   click **📋**.
3. In the modal:
   - **Name**: `Process transcript`
   - **Task**: `Summarise this transcript and extract action items:\n\n{{text}}`
4. Click **Save template**.

Every call to `quarterdeck://intake?template=Process+transcript&text=...` will
now start a new session from that exact conversation context, with the transcript
substituted in.

---

## Testing

Quick sanity check from the terminal:

```bash
# Direct task (no template needed)
open "quarterdeck://intake?task=Say+hello+and+stop&cwd=$HOME"

# Template (replace with a real template name)
open "quarterdeck://intake?template=My+Template&text=hello+world"
```

Quarterdeck must be running. If it is not, macOS will launch it first and then
deliver the URL.

---

## API equivalent

The URL handler is a thin wrapper around the REST API. You can call it directly
from any script that has access to `localhost`:

```bash
curl -s -X POST http://127.0.0.1:19418/api/intake \
  -H "Content-Type: application/json" \
  -d '{
    "template": "Process transcript",
    "vars": {"text": "Alice: hello\nBob: hi"},
    "cwd": "/Users/me/project"
  }' | python3 -m json.tool
```

This bypasses the URL encoding step entirely and handles large texts without
URL length limits.

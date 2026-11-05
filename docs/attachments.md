# Attaching a file to an answer

In the chat, the paperclip next to the answer box attaches **one PDF, Word (.docx), PowerPoint
(.pptx) or JSON file** (up to 5 MB). The server reads its text and throws the file away; the text
is sent with the answer, and the AI reads it **exactly as if the user had typed it**. The user
sees the file as a chip (name, number of characters, remove) before pressing Send, and the chat
shows it folded under the answer ("Attached file: idea.docx (1,234 characters)") with the text a click away.

Code: `backend/app/attachments/` (reading, markers), `backend/app/api/attachments.py` (route),
`frontend/src/api/attachments.ts` and the composer in `ConversationScreen.tsx`.

## How it works

1. `POST /api/attachments?name=<file name>` with the raw file as the body (session, same-origin
   check and CSRF token, like every state-changing request). The reply is `{filename, kind,
   characters, truncated, block}`. **Nothing is stored**: the file is read in memory.
2. The browser appends `block` to the typed text and sends it through the normal answer route
   (`POST /api/conversation/turns`), so retries, revisions and the stored conversation all work as
   before. The answer limit is now 20,000 characters: up to 4,000 typed plus one file's text (cut
   at 15,000 characters, at a word, and flagged "shortened").
3. The conversation, the canvas draft and the appendix then hold the text like any other answer.

The text sits between `[[Attached file: name | kind]]` and `[[End of attached file]]` markers. A
file cannot forge them: lookalikes inside it are broken up.

## What is read

| Type | How |
| --- | --- |
| PDF | text of up to 100 pages (`pypdf`); password-protected files are refused |
| Word | the paragraphs of `word/document.xml`, in order |
| PowerPoint | the text of each slide, in slide order |
| JSON | pretty-printed as text |

Scanned documents (no text layer) are refused with "No text was found". The Word and PowerPoint
files are read directly from their XML; no macros or embedded objects are touched.

## Safety rules (each has a test)

* **Type by extension and by content**: a file whose content is not what its name says is
  unreadable. Anything else (`.txt`, `.docm`, images, archives) is refused.
* **Size**: 5 MB upload (checked as it streams, not after), each XML part inside a Word/PowerPoint
  file at most 10 MB, at most 5,000 entries; deep JSON, damaged and hostile files are just
  "could not be read". XML is parsed with `defusedxml`, so entity bombs and external entities
  (local-file reads) are refused.
* **A file is the user's words, not instructions**: the AI is told to record its facts and treat
  everything else as data; an instruction hidden in a file gets the same redirect as one typed.
  Only **typed** words can choose the priority or say "skip" / "I don't know": a file that says
  "Priority: Must-have" never sets it.
* **Content-free logs**: reading a file logs only its type. File names, text and bytes are never
  logged, and error messages are fixed wording.
* **Shown as text**: a file's text is rendered as plain text in the chat, never as markup.

## Limits

* One file per answer. Images, scanned PDFs, spreadsheets and plain text files are not supported.
* PDF reading uses a library that has had denial-of-service bugs with crafted files. The size and
  page limits bound the work, but there is no time limit per file; rate limiting belongs at the
  gateway (the WAF rule already required for the access code).
* The files were checked with files made by this app's own PDF, Word and PowerPoint writers, not
  with documents from every version of Word, PowerPoint or PDF tools.

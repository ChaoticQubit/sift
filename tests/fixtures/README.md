# Test fixtures

A **tiny fake DYI export** mirroring the real ZIP layout (F1):

- `your_instagram_activity/saved/saved_posts.json` — real export shape
  (`saved_saved_media` + `string_map_data`), incl. a mojibake title and one
  malformed entry (skipped + counted, never silently dropped)
- `your_instagram_activity/saved/saved_collections.json` — collection name
  (mojibake emoji) mapped onto one saved post
- `your_instagram_activity/messages/inbox/self_notes/` — the note-to-self thread
  (exactly ONE participant): a shared reel with `share_text` caption, a plain
  text note, one photo attachment present in the zip and one missing
- `your_instagram_activity/messages/inbox/groupchat_decoy/` — 3 participants;
  must be ignored

Mojibake strings are checked in exactly as Meta emits them (UTF-8 bytes
escaped as latin-1 code points — includes invisible C1 controls; don't "fix"
them in an editor). The `dyi_zip` conftest fixture assembles this tree into a
ZIP at test time and injects the one present media file (no binaries in repo).

No test may hit Instagram or a paid API. Keep everything here small and synthetic.

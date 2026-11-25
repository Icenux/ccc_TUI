# Notes

ccc keeps your **notes** as plain Markdown files, so you can write them
anywhere and nothing but ccc is needed. Press `N` (Shift+N) to open them: the
list of notes on top, the selected one rendered below it.

The files live in `~/.local/share/ccc/notes/` (next to ccc's database) — open
them in any editor, back them up or put the folder under git. A name can
start with a folder: `ideas/api` is the file `ideas/api.md`.

The [Android app](android-api.md) reads and writes the same notes, so what
you write on the phone is here a moment later, and the other way round.

## Keys

| Key | |
|---|---|
| `+` | write a new note: ccc asks for its name, then opens your editor |
| `e` / `Enter` | edit the selected note in the editor |
| `r` | rename the note, or move it to another folder (`work/ideas`) |
| `Delete` | delete the note — the file moves to `deleted-notes/` next to the notes, it is not erased |
| `/` | search the names and the text of the notes (`Esc` clears it) |
| `Esc` | back |

## The editor

ccc steps aside while the editor runs and takes back what you saved. Which
editor it is, you choose in [Settings](settings.md) (Auto: `$VISUAL` /
`$EDITOR`, else the first one installed, e.g. nano).

## Edited on both sides

Should the same note change while you edit it (say, you save it on the
phone), ccc does not overwrite it: your version is saved next to it as
`name (conflict).md`, and you merge the two as you like.

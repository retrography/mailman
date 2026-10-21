# Mailman

Sorts the mail arriving in a Gmail mailbox, and optionally an Outlook one, by rules you can read and change: it
labels, files, and deletes what you never wanted. A daemon per mailbox handles new mail; this interface (in the sidebar) shows what it did and why,
and lets you change every rule, list and question, with a test on real emails before each save.

## Before you start

- **A TypeSafe API key.** The classifier (TypeSafe Jev) reads each email.
- **Your own Google OAuth client.** In the Google Cloud console: enable the Gmail API, create an OAuth client
  of type "Desktop app", and set the publishing status (Audience) to "In production" — in "Testing" the
  sign-in expires after a week. You do not need Google's verification for your own use.

## Setup

1. Configuration tab: enter the TypeSafe API key and the Google client ID and client secret. Start the app.
2. Open Mailman. Health → **Connect Gmail**: sign in with Google in a new tab; your browser then lands on a
   `localhost` page that does not load, which is expected. Copy that address and paste it into Mailman.
3. Profile: replace the invented example person with yourself (names, addresses, languages, countries).
   Classifier: the question about the addressee has examples with the invented names; adjust them.
4. Mail that arrives from now on is handled. Existing mail is left alone.

Coming from another installation? Export it there (Backup → Export, or `mailman export`) and upload the file
under Backup → Import. The configuration alone is enough to sort mail; the full bundle adds the log and the
test set.

## A second mailbox: Outlook

An Outlook.com (or Microsoft 365) mailbox can be sorted by the same rules, next to Gmail.

1. Register an app in the Microsoft Entra admin center (App registrations → New registration). Let it accept
   personal Microsoft accounts, leave the redirect address empty, and under Authentication turn on "Allow
   public client flows". Copy its Application (client) ID.
2. Configuration tab: enter it as the Microsoft client ID (and, for a work or school mailbox, the tenant).
   Restart the app.
3. Profile: add the Outlook address to your addresses. Until it is there, no Outlook mail is handled.
4. Health → **Connect Outlook**: open Microsoft's page (on any device), type the code Mailman shows, and
   approve. Outlook mail that arrives from then on is handled.

In an Outlook mailbox:

- A label is a folder of that name, made the first time it is needed (`Travel/2026` is a subfolder). Your own
  folders count as labels too, including those inside Inbox.
- A message is in one folder. Filing puts it in the folder of its first label, or in Archive when it has
  none. An email kept in the inbox stays there; its labels are remembered out of sight, so searches and
  flags still see them.
- Delete and spam move mail to Deleted Items and Junk Email. Undoing brings it back where it was.
- To block a sender, move one of their emails to the Blocked folder, from anywhere. Mailman makes the folder
  when it starts.
- New mail is noticed within a minute (`outlook.poll_seconds` in Settings), not at once.
- Searches in jobs and flags may use only `from:`, `label:`, `in:`, `older_than:` and `newer_than:` (and their
  negations); a job with any other word fails for the Outlook mailbox.

## Where things are kept

- **The configuration files**: in Home Assistant's configuration folder, `mailman/`. Created on first start;
  an update of the app never overwrites them. Plain YAML, part of Home Assistant backups, fine to keep in git.
- **Everything else**: the log (`mailman.db`), the test set, caches, and the mailbox sign-ins (`tokens.json`),
  in the app's private folder, which other apps cannot read, and in the app's own backup. The log and the test
  set hold personal mail; whoever has `tokens.json`, or a backup that contains it, can read the mailboxes.

Versions before 0.11.0 kept all of this in `/addon_configs/<…>_mailman/`. The first start of a newer version
copies it over and checks every file; the old folder is left as it was, with a `MOVED.txt` note, and can be
deleted afterwards.

## Alerts

When something needs attention (the classifier is out of credits, a mailbox is disconnected, mail is waiting),
Mailman says so on every page and in Home Assistant: `binary_sensor.mailman_problem`,
`sensor.mailman_waiting`, and a notification that disappears when the problem is over.

## Blocking a sender

Move one of their emails to **Blocked**: a label in Gmail, a folder in Outlook. Within a few minutes the sender
is on the blocked list (Lists → blocked), that email and the sender's other mail go to Trash, and their new mail
is trashed as it arrives.

To unblock, take the sender off the blocked list and save. Their mail loses the Blocked label, leaves Trash and
goes through the rules again, as if it had just arrived: it may be filed, labelled or deleted like any new
email. The save dialog says so before anything changes.

## What it does to your mail

By default it moves mail to Trash (recoverable for 30 days), adds and removes labels, and takes mail out of
the inbox. Nothing is deleted for good. Every action is in Logs, with the rule that decided and the facts
behind it, and can be undone from there.

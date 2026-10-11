# Changelog

All notable changes to Mailman. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) as described in the README.

## [0.10.1] - 2026-10-12

### Changed
- The page with actions and triggers is called Automations, at `#/automations`; its tabs stay Actions and
  Triggers. The old addresses `#/actions` and `#/jobs` no longer open it.
- An action's `then_run` field is labelled "post".
- Labels that apply to any mailbox no longer say Gmail: "search", "label", the Outcomes page, lookups, list
  sources, and the label clean-up.

## [0.10.0] - 2026-10-11

### Added
- An action setting `then_run`: the emails an action changed then go through an action that applies the rules,
  at the daemon's next round. The starter's `unblock` runs `classify`, so unblocked mail is filed, labelled or
  deleted like new mail instead of all landing in the inbox. The Actions page has a field for it.

Existing installations keep their configuration: add `then_run: classify` to the action `unblock` (Actions).

## [0.9.0] - 2026-10-11

### Added
- A list setting `on_removed_entry`: a job that runs, in every mailbox, for each entry you take off the list in
  the interface. The save dialog lists those entries before you save, and Health shows what each run did.
- The starter configuration's blocked list runs `unblock` for a sender you take off it, and a new action
  `trash_blocked` sends what you move to Blocked to Trash.

### Fixed
- A sender taken off the blocked list came back at the next block. Their mail kept the Blocked label, and the
  list is filled from that label. With `on_removed_entry: unblock` the label comes off with the entry.
- Mail you moved to Blocked stayed there. `trash_blocked` sends it to Trash; until now only the sender's
  other mail was trashed.

Existing installations keep their configuration. To get both fixes, add `on_removed_entry: unblock` to the
blocked list (Lists), and the action `trash_blocked` with a trigger `label_added: Blocked` (Actions).

## [0.8.0] - 2026-10-11

### Changed
- Outlook: a label is a folder of that name instead of a category, which no mail client shows. Folders are
  made when first needed (`Travel/2026` is a subfolder), and your own folders count as labels. Filing puts
  mail in the folder of its first label, or in Archive without one.
- Outlook: moving an email to the Blocked folder blocks its sender, from wherever it was moved. Mailman makes
  the folder when it starts.

### Fixed
- Outlook: undoing a delete or a spam brings the email back where it was, not to Archive.

## [0.7.1] - 2026-10-10

### Added
- Logs: a Mailbox column says which mailbox (Gmail or Outlook) each email was handled in.

## [0.7.0] - 2026-10-10

### Added
- A second mailbox: Outlook.com or Microsoft 365, through Microsoft Graph, sorted by the same rules as Gmail.
  Connect it on Health (a code typed on Microsoft's page) or with `mailman auth --outlook`; it needs your own
  Microsoft app registration (options `outlook_client_id` and `outlook_tenant`). Labels become categories,
  filing moves mail to Archive, and new mail is noticed within a minute. The Documentation tab lists what
  differs from Gmail.
- Settings `outlook.poll_seconds` and `outlook.archive_folder`.

### Changed
- One daemon runs per connected mailbox, and Health shows each. `mailman daemon` starts one process per
  mailbox; `--account` runs a single one.
- The log records which mailbox each email is in, and undo and "This was wrong" act there. An existing log is
  upgraded at the first start; its emails count as Gmail's.
- A job run from Actions runs in every connected mailbox.

## [0.6.0] - 2026-10-09

### Added
- A Test set page: every email the configuration is checked against (the cached sample and the emails you
  marked), what the rules do with each now, and whether that is what you expect. Open one to read it, set or
  clear the outcome and labels you expect, add a note, edit the classifier's stored answers, or remove it from
  the test set.

### Changed
- The Automations page is now called Actions, at `#/actions`; the old `#/jobs` address still opens it.

## [0.5.1] - 2026-10-05

### Fixed
- After an update the browser could show the new page with the previous version's script: the Backup link
  opened Logs, and Import was still on Health. The page is now sent as one document, script and styles
  included, and is never cached.

## [0.5.0] - 2026-10-05

### Added
- A Backup page with Export (the configuration, or everything including the log and the test set) and Import.

### Changed
- Import moved from Health to the Backup page.

### Fixed
- An import started from a page that was still an older version failed with "[object Object]". The server now
  accepts the upload in either form.

## [0.4.4] - 2026-10-05

### Fixed
- A page left open across an update kept running the old version's code against the new server; the import
  then failed with "[object Object]". The page now notices the update and offers to reload, and such an
  error is shown in words.

## [0.4.3] - 2026-10-05

### Added
- `mailman export --config-only`: only the configuration files, without the log and the test set.

### Fixed
- The import failed in the browser with "Load failed". The bundle is now sent as small text pieces, the same
  kind of request as the rest of the interface, and a failure says at which piece and why.
- A "Gmail is not reachable" alert left by an earlier run is cleared when the daemon starts and Gmail answers.

## [0.4.2] - 2026-10-05

### Changed
- The daemon handles no mail while the mailbox that signed in is not one of the profile's addresses (a fresh
  install with the invented profile, or before an import). An alert says so.
- The import is uploaded in small pieces and waits for the app to come back.

### Fixed
- After signing in on a fresh install, the daemon failed with "Invalid credentials": it signed in to the mail
  server with the profile's address instead of the account that had signed in.

## [0.4.1] - 2026-10-05

### Fixed
- A fresh install showed "The daemon is not running — start it again" before Gmail was connected. The daemon
  waits for the sign-in and starts by itself; only "Gmail is not connected" is shown now.

## [0.4.0] - 2026-10-05

First public release.

### Added
- The rule engine, driven entirely by configuration files: profile, lists, flags, classifier questions,
  rules in four stages (filter, label, inbox, rescue), outcomes, automations and triggers.
- The daemon: watches the inbox, classifies new mail, and retries mail that could not be handled.
- The web interface: logs with the reasons behind each decision, forms and a YAML editor for every
  configuration file, a test on stored emails before each save, and undo.
- Label clean-up from the interface: preview, review of every removal, then apply.
- Alerts on every page and, as a Home Assistant app, as sensors and a notification.
- The Home Assistant app: Ingress, options for the classifier key and the Google client, storage in the
  app's own folder, and an image for amd64 and aarch64.
- Gmail sign-in from the interface, for a machine without a browser.
- `mailman export` and an import step, to move an installation.
- A starter configuration built around an invented person.
- An icon and a logo for the app.

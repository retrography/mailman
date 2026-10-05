# Changelog

All notable changes to Mailman. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) as described in the README.

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

"""Outcomes (config/outcomes.yaml): what each outcome does in Gmail.

  do      operations, in order: trash · untrash · {add_labels: […]} · {remove_labels: […]} · mark_read · star
          `$labels` stands for the labels the rules gave the email; `user_labels` for every label you made
  log_as  the word the log uses for it (default: the outcome's name)
  undo    the operations that reverse it
Label changes of one outcome go to Gmail as a single call. Labels that do not exist are created.
"""

from __future__ import annotations

from mailman import gmail

SYSTEM = {"INBOX", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT"}


class Labels:
    """Gmail label ids by name (cached; created when missing)."""

    def __init__(self, svc):
        self.svc, self.ids = svc, {}

    def id(self, name: str) -> str:
        if name in SYSTEM:
            return name
        if name not in self.ids:
            self.ids[name] = gmail.ensure_label(self.svc, name)
        return self.ids[name]

    def user_labels(self) -> list[str]:
        return gmail.user_label_ids(self.svc)


def plan(outcome: dict, labels: list[str]) -> tuple[bool, list[str], list[str]]:
    """(trash?, label names to add, label names to remove) for an outcome's operations."""
    trash, add, remove = False, [], []
    for op in outcome.get("do", []):
        if op == "trash":
            trash = True
        elif op == "untrash":
            remove.append("TRASH")
        elif op == "mark_read":
            remove.append("UNREAD")
        elif op == "star":
            add.append("STARRED")
        else:
            for key, target in (("add_labels", add), ("remove_labels", remove)):
                for n in op.get(key, []):
                    target.extend(labels if n == "$labels" else [n])
    return trash, add, remove


def apply(svc, label_ids: Labels, outcome: dict, labels: list[str], ids: list[str], batch: bool = False) -> bool:
    """Perform an outcome on messages (`batch`: one call per 1000). Returns whether anything was sent."""
    trash, add, remove = plan(outcome, labels)
    add_ids = [label_ids.id(n) for n in add]
    remove_ids = [i for n in remove
                  for i in ([l for l in label_ids.user_labels() if l not in add_ids] if n == "user_labels"
                            else [label_ids.id(n)])]
    if not ids or not (trash or add or remove):
        return False
    if len(ids) == 1 and not batch:
        if trash:
            gmail.trash(svc, ids[0])
        if add or remove:
            gmail.relabel(svc, ids[0], add_ids, remove_ids)
        return True
    if trash:
        add_ids = [*add_ids, "TRASH"]
    change = {k: v for k, v in (("addLabelIds", add_ids), ("removeLabelIds", remove_ids)) if v}
    for k in range(0, len(ids), 1000):
        gmail._execute(svc.users().messages().batchModify(userId="me", body={"ids": ids[k:k + 1000], **change}))
    return True


def action_name(name: str, outcome: dict, labels: list[str], acted: bool) -> str:
    """The log's word for what was done: 'trash', 'file Kids,Bills', 'label Kids', 'none'."""
    word = outcome.get("log_as", name) if acted else "none"
    return word + (" " + ",".join(labels) if labels else "")

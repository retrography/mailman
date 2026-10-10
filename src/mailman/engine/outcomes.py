"""Outcomes (config/outcomes.yaml): what each outcome does in the mailbox.

  do      operations, in order: trash · untrash · {add_labels: […]} · {remove_labels: […]} · mark_read · star
          `$labels` stands for the labels the rules gave the email; `user_labels` for every label you made
  log_as  the word the log uses for it (default: the outcome's name)
  undo    the operations that reverse it
Label changes of one outcome go to Gmail as a single call. Labels that do not exist are created. In an Outlook
mailbox a label is a category and INBOX / TRASH / SPAM are folders (mailman.outlook).
"""

from __future__ import annotations


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


def apply(box, outcome: dict, labels: list[str], ids: list[str], batch: bool = False) -> bool:
    """Perform an outcome on messages of a mailbox (`batch`: many at once). Returns whether anything was sent."""
    trash, add, remove = plan(outcome, labels)
    if not ids or not (trash or add or remove):
        return False
    box.apply(ids, trash, add, remove, batch=batch)
    return True


def action_name(name: str, outcome: dict, labels: list[str], acted: bool) -> str:
    """The log's word for what was done: 'trash', 'file Kids,Bills', 'label Kids', 'none'."""
    word = outcome.get("log_as", name) if acted else "none"
    return word + (" " + ",".join(labels) if labels else "")

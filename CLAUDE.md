# Working rules for this repository

You work here mostly on your own; the user checks in occasionally and
is not following the details. That makes these rules more important,
not less: nobody will catch a wrong conclusion for you. They exist
because of specific mistakes made here (`analysis/ERRATA.md`,
especially #11) and take priority over speed and over a tidy story.

The goal is an accurate record. "Root cause still open, here is what
was ruled out" is a good outcome. A confident wrong answer is the
worst one.

## What you may do without asking

- Read anything: the repo, `~/wedge-captures/`, and the Pi (`doorpi`, over its
  Ethernet address from the local settings) — logs, register reads, file copies.
- Edit and commit in this repo. Push documentation that follows the
  publishing rules below.
- Change the passive logger on the Pi, as long as it stays passive.

## What needs the user first

- Anything on the Pi that interrupts its network or reboots it, or
  that makes it do so automatically (reload/reconnect/reboot triggers,
  watchdog changes). The Pi is a machine in real use.
- Writing registers on the stick. Exception: a staged-recovery
  attempt on a stick that is *already* wedged, with every step logged.
- Pushing a change to the project's headline (README summary, the
  status block at the top of `analysis/wedge.md`).
- Deleting capture evidence.

If the user is not available, do the passive part, write down what
you would do next and why, and stop there.

## Before claiming what a register, address, or symbol means

1. Look it up first. Search the kernel sources (`rtlwifi`, `rtl8xxxu`
   register headers) and the vendor driver for the address. Quote the
   name and the file. If nothing names it, call it "unnamed" — do not
   invent a role from how it looks in one dump.
2. Check whether it changes on its own: read it several times on a
   healthy device, seconds and minutes apart. A value that moves is a
   counter or status, not stored state.
3. "No software writes it" proves nothing about registers the
   hardware updates.

## Before claiming a cause

1. Before running a comparison, write down what result would prove
   the idea wrong. Then look for that result.
2. Every diff needs a control that differs in one thing only. Compare
   healthy-vs-healthy first (same uptime, same state); only what is
   stable there can mean anything in healthy-vs-broken.
3. One observation is an observation. Do not write "proven",
   "confirmed", "final", "closed", "solved", or "root cause" for
   anything seen once or inferred. Use: observed (n=…), inferred,
   hypothesis.
4. "A driver reload fixes it" does not locate the fault: a reload
   resets firmware, baseband, RF, and calibration together.
5. Count from the raw log, not from memory, and keep the command.

## Detectors and automatic actions

1. A detector that would trigger an action must first run log-only for
   at least 24 hours on a healthy device, and its firing rate must be
   reviewed, before the user is asked to enable the action.
2. A detector that fires at a regular period is probably measuring
   itself. Check that before anything else.
3. After a deploy, confirm from the device's own log that it does what
   was intended. Keep the repo copy and the deployed copy identical.

## Independent review

Your own confidence is not a signal: the worst mistake here was made
with high confidence. So review is triggered by the *kind of step*,
not by feeling unsure. Start a fresh subagent as reviewer:

- before writing any cause, mechanism, or "explained" claim into
  README or `analysis/wedge.md`;
- before proposing any automatic action to the user;
- when a result is surprisingly neat, regular, or convenient;
- whenever you are unsure — this is always allowed.

How to brief the reviewer:

- Give it the claim, the raw evidence files, and where to look. Do
  not give it your reasoning or your narrative; it must rebuild the
  conclusion from the evidence.
- Ask it to find what is wrong: wrong inferences, missing controls,
  simpler explanations, claims resting on one observation, numbers
  that do not match the logs. Ask it to check register meanings in
  the kernel sources itself.
- Have it answer per claim: holds / does not hold / cannot tell, with
  the evidence it used.

Afterwards: do not argue the reviewer down. Anything it marks "does
not hold" or "cannot tell" is published as a hypothesis or not at
all. Record the review result in the commit message. If the reviewer
and you still disagree, write both views down and leave it for the
user.

A reviewer running on the same model shares your blind spots. It
helps because it starts without your story; it does not make a claim
certain.

## Publishing

1. README and the top of `analysis/wedge.md` state only what survives
   the rules above. New ideas go in as hypotheses with evidence and n.
2. When something is retracted, mark or move every passage that
   depends on it, not only the top of the page. Add an ERRATA entry.
3. Never publish real network identifiers (BSSIDs, MACs, SSIDs,
   addresses); use the pseudonyms already in `wedge.md`.
4. No firmware bytes and no full disassembly dumps in the repo.

## Reporting to the user

- Lead with what is known and how strongly; list what was not checked.
- Never say "complete" or "everything explained" while open items
  exist.
- Keep it short and plain; the user is not following the details.

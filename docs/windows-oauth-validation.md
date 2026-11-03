# Windows OAuth validation — 2026-11-03

Native Windows, Python 3.12.14, pywin32 312. No WSL was used.

## Results

- Before implementation: 638 passed, 252 failed.
- Final full source suite: 727 passed, 172 failed, 1 skipped.
- Every remaining failing test name was also in the baseline.
- OAuth storage/login/config/CLI/locking subset: 99 passed, 1 skipped, against both
  source and the installed wheel.
- The skipped native reparse-point integration requires Windows symlink privilege,
  which this account lacks. Hardlink rejection ran successfully.
- Wheel and source distribution built successfully; installed environment passes
  dependency checks. Real stdio MCP initialization reports Zendesk and 101 tools.
- No macOS/Linux tests were run locally. CI retains the Linux suite and adds the
  OAuth subset to Windows/macOS package jobs.

## Reviewed behavior

Protected ACL creation, unsafe ACL and hardlink rejection, same-handle validation,
failed-write preservation, concurrent refresh rotation, cross-process locks and
callback validation were exercised. Independent review caught a Windows
read/publication conflict. The new regression fails when the I/O lock is disabled
and passes with the fix; the fix serializes short local reads and writes using a
lock separate from the network refresh lock.

## Remaining platform limits

The 172 baseline failures include unrelated POSIX assumptions in approvals,
attachment caches, image uploads, legacy migration and test socket setup.
The existing read-only catalog assertion expects write_disabled for every write
tool; two image-upload tools instead return unsupported on Windows. These calls
are still refused. This patch does not establish full Windows feature parity.

## Installed-package integration

Browser login in Chrome completed after user consent. A live identity probe
confirmed OAuth authentication with read-only policy, and a newly launched
installed MCP server successfully retrieved tickets. No account identifiers,
credentials or customer data are included in this validation record.

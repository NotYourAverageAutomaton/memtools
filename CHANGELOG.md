# Changelog

## v1.4.1 (2026-11-13)

Fixes from an outside review of v1.4, each reproduced on a scratch copy before it was fixed, each with a control in
`tests/test_review_fixes.py` that fails on v1.4 and passes on v1.4.1 (`python3 tests/test_review_fixes.py <v1.4 tools> --control`).

- vault-verify, git_unpushed: a malformed dirty exemption returned "ERROR", which no tally counted, so the run reported 0 problems.
  It is now UNKNOWN, as the method requires: an unknown claim is never a pass.
- vault-verify, grep_absent, git_tracked, git_push_blocked: a relative path is now anchored to the notes folder, like the other
  kinds. Before, the answer depended on the folder you ran from (a file could pass grep_in and grep_absent at once).
- vault-verify, parse_claims: a quoted claim is taken whole, so a pattern may contain " #"; an unquoted claim still loses a
  trailing " # comment", as YAML does.
- vault-recall, vault-index, vault-applied: an applied-log entry written as [[page|alias]] or [[page#heading]] now counts as page.
- vault-close: removing the closing "---" no longer eats trailing "-" characters from the last frontmatter field. And --check now
  reads vault-verify's report by blocks: v1.4 matched lines by substring, so it never saw its own page's failing claims (a failing
  claim passed --check) and could show another page whose name ends the same. This last one was found while writing the control
  for the review's report about the substring match. And a report with NO row for the page (vault-verify refused, because this is
  not the configured home_host, or printed nothing) now fails --check: no report is not a pass.
- vault-park --unpark: the "PARKED ..." next_action written by the park is removed.

Thanks to an outside review for the first six.

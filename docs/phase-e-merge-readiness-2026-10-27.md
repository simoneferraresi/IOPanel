# Phase E merge readiness — 27 October 2026

## Recommendation

Do not merge Phase E yet. Keep the current PRs as drafts while native MATLAB acceptance and the intended physical CT400/Vimba acceptance are pending, and while the final local integration-hardening commit is unavailable for GitHub review/CI. The application candidate passed the hardware-independent Windows and frozen-package gates recorded in [the qualification report](phase-e-final-qualification-2026-10-27.md).

## Current pull request stack

| PR | Stage | Current base | Observed state |
| --- | --- | --- | --- |
| [#266](https://github.com/simoneferraresi/IOPanel/pull/266) | E1 CT400 lifecycle | `main` | Open draft; mergeable; historical jobs passed |
| [#267](https://github.com/simoneferraresi/IOPanel/pull/267) | E2 camera/TaskRunner | E1 | Open draft; mergeable; historical jobs passed |
| [#268](https://github.com/simoneferraresi/IOPanel/pull/268) | E3 MATLAB lifecycle | E2 | Open draft; mergeable; historical jobs passed |
| Local `fix/phase-e-integration-hardening` | E3 future-finalizer correction | local E3 | Commit `4de5211351caa46fd3cfa0079866db35858c8348`; no remote branch/PR/CI |

Origin/main was `63c0c4b4bdaf1a2c931e63dce13e20f88da5c9f6` when fetched. The main update since the original Phase E baseline was documentation-only with no source overlap. E1/E2/E3 remote trees equal the local validated trees. Current GitHub repository metadata permits merge-commit, rebase, and squash strategies; auto-merge and automatic branch updates are disabled. Exact branch-protection rules were not available through the inspected connector, so required status checks and reviewer policy must be checked in GitHub before final merge.

## Rehearsal findings

Two local-only rehearsals were performed from the fetched main snapshot. No rehearsal branch was pushed.

1. **Merge commits, retargeting each dependent PR after its predecessor merges:** E1 → E2 → E3 → hardening merged without conflicts. Final tree `f5268c7c8e8cd598a375efd09ea501a1464a1e10`. Relative to the tested candidate, only the newer documentation changes from PR #265 differed. E1/E2/E3 source and test code were exact. The rehearsal collected 1,617 tests and reran the 32 focused MATLAB manager/cooperative-shutdown tests successfully.
2. **Squash on the existing stacked PR branches:** after squashing E1 onto main, an unchanged E2 branch showed the cumulative E1+E2 diff; after squashing E2, E3 likewise showed the cumulative E1+E2+E3 diff. Squashing E3 on top of squashed E1+E2 conflicted in camera/control/main-window code and three lifecycle tests. This is not a safe path for the existing stacked heads without rebuilding successor branches.
3. **Squash with successor branches:** rebuilding successor branches from updated main and applying each stage patch preserved stage-specific diffs. The final rehearsal tree exactly matched the merge-commit rehearsal tree. This requires new successor PRs and deliberate porting of review history; no remote changes were made.

## Can E1/E2 proceed without E3?

E1 has no dependency on E2/E3, and the E1 PR states that explicitly. E2 depends on E1 but not E3. Their source tests and simulation evidence are independent of MATLAB. In principle, E1 could merge first and E2 could follow while E3 remains a draft. However, CT400 and physical Vimba acceptance are both still pending, and the integrated source-correction commit is not on either remote branch. I recommend holding both until the responsible instrument owners provide the acceptance required by project policy and reviewers decide whether the physical gate is mandatory for these lifecycle changes. E1/E2 should not be represented as physically qualified.

E3 cannot merge in its current published state for this qualification: the finalizer correction exists only in the local follow-up commit. That local commit's parent is the local E3 SHA `f4edfa9…`, while remote E3 is `fae56c9…`; their trees match, but their commit identities do not. A new follow-up PR created directly from the remote E3 head would add a second E3-stage diff after E3, while applying the local commit identity as-is would not provide the intended Git ancestry. The cleanest correction path is a **successor E3 draft based on remote E2** containing the E3 patch plus the finalizer correction, created without rewriting or force-updating existing PR history. Preserve #268 as the review record and mark it superseded only through normal maintainer-approved PR handling. Re-run/review CI on that exact successor tree. This also ensures E3 enters main with the fix in the same merge.

## Suggested integration sequence after gates pass

1. After the upload review block is cleared, create a successor E3 branch from remote E2 `04cf3f64…`. Apply the remote E3 patch and the local finalizer correction; compare the full resulting tree with the tested source tree `00020968…` (allowing only the reviewed newer-main documentation files). Open a draft successor E3 PR to remote E2 and run fresh CI. Do not force-update #268 or reuse a branch with mismatched local/remote ancestry.
2. Merge #266 (`87791a2…` → `main`) using a merge commit after its required acceptance/review. Fetch again. Retarget #267 (`04cf3f…`) to `main`, inspect the GitHub compare view and local diff to ensure it contains only E2, wait for fresh required CI/reviews, then merge by merge commit.
3. Fetch again and verify E2 is in `main`. Retarget the successor E3 PR to `main` only if its base branch still names the now-merged E2 branch; inspect that the PR diff is exactly E3 plus the finalizer correction. Require native acceptance on this exact tree, review, and fresh CI, then merge by merge commit.
4. Keep all PRs in draft until required acceptance and reviewer sign-off are attached to the exact final SHA. Confirm branch protection, required checks, approvals, and release steps directly in GitHub. No merge is approved by this report.

Useful read-only verification after each base/head change:

```powershell
git fetch origin --prune
git diff --stat origin/main...origin/<phase-branch>
git diff --name-status origin/main...origin/<phase-branch>
git merge-base origin/main origin/<phase-branch>
```

Compare the result with the expected stage patch and the GitHub PR's Files changed view. Stop if an earlier stage reappears or a conflict changes file content. Use GitHub's merge-commit action only after checks and reviews pass; do not update refs by force.

If project policy requires squash merging, create clean successor branches from updated main with only that phase's patch and open successor PRs; do not simply retarget the existing stacked heads. Repeat collection and relevant regressions on the post-squash rehearsal result before integration.

## Stop conditions

- Hardening source upload remains blocked by automatic approval review; do not bypass through another upload path.
- A native MATLAB case times out, fails FIG assertions, returns without orderly Engine disposal, or leaves an unattributed process.
- CT400 or Vimba acceptance is absent for a phase that changes its ownership path, or the operator cannot establish device identity/safe state.
- Any PR's base/head diff is cumulative or conflicts with the expected stage, required CI fails/is absent, or repository branch protection remains unchecked.

No merge, PR retarget, branch update, forced push, hardware call, or MATLAB launch was performed as part of these rehearsals. Temporary rehearsal worktrees and evidence were retained.

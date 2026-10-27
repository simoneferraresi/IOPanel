# Phase E merge readiness — 27 October 2026

## Recommendation

Do not merge Phase E yet. Keep the current PRs as drafts while native MATLAB FIG/shutdown acceptance remains incomplete after an R2024b Fatal Startup Error (`Transport stopped`), and physical CT400/Vimba acceptance remains pending. PR #270's published head passed all four CI jobs. The runner update and follow-up PlotWidget fix are local; GitHub CLI login was denied by this environment's HTTPS socket restriction. The application candidate passed the hardware-independent Windows and frozen-package gates recorded in [the qualification report](phase-e-final-qualification-2026-10-27.md).

## Current pull request stack

| PR | Stage | Current base | Observed state |
| --- | --- | --- | --- |
| [#266](https://github.com/simoneferraresi/IOPanel/pull/266) | E1 CT400 lifecycle | `main` | Open draft; mergeable; historical jobs passed |
| [#267](https://github.com/simoneferraresi/IOPanel/pull/267) | E2 camera/TaskRunner | E1 | Open draft; mergeable; historical jobs passed |
| [#268](https://github.com/simoneferraresi/IOPanel/pull/268) | E3 MATLAB lifecycle | E2 | Open draft; mergeable; historical jobs passed |
| [#270](https://github.com/simoneferraresi/IOPanel/pull/270) | E3 finalizer follow-up + qualification files | #268 | Open draft; current head `f2e0b272`; all four jobs passed |
| Local `fix/phase-e-integration-hardening` | Latest runner safety update | local only | Adds fail-closed process attribution/cleanup checks; publication awaits restored GitHub CLI authentication |

Origin/main was `63c0c4b4bdaf1a2c931e63dce13e20f88da5c9f6` when fetched. The main update since the original Phase E baseline was documentation-only with no source overlap. E1/E2/E3 remote trees equal the local validated trees. Current GitHub repository metadata permits merge-commit, rebase, and squash strategies; auto-merge and automatic branch updates are disabled. Exact branch-protection rules were not available through the inspected connector, so required status checks and reviewer policy must be checked in GitHub before final merge.

## Rehearsal findings

Two local-only rehearsals were performed from the fetched main snapshot. No rehearsal branch was pushed.

1. **Merge commits, retargeting each dependent PR after its predecessor merges:** E1 → E2 → E3 → hardening merged without conflicts. Final tree `f5268c7c8e8cd598a375efd09ea501a1464a1e10`. Relative to the tested candidate, only the newer documentation changes from PR #265 differed. E1/E2/E3 source and test code were exact. The rehearsal collected 1,617 tests and reran the 32 focused MATLAB manager/cooperative-shutdown tests successfully.
2. **Squash on the existing stacked PR branches:** after squashing E1 onto main, an unchanged E2 branch showed the cumulative E1+E2 diff; after squashing E2, E3 likewise showed the cumulative E1+E2+E3 diff. Squashing E3 on top of squashed E1+E2 conflicted in camera/control/main-window code and three lifecycle tests. This is not a safe path for the existing stacked heads without rebuilding successor branches.
3. **Squash with successor branches:** rebuilding successor branches from updated main and applying each stage patch preserved stage-specific diffs. The final rehearsal tree exactly matched the merge-commit rehearsal tree. This requires new successor PRs and deliberate porting of review history; no remote changes were made.

## Can E1/E2 proceed without E3?

E1 has no dependency on E2/E3, and the E1 PR states that explicitly. E2 depends on E1 but not E3. Their source tests and simulation evidence are independent of MATLAB. In principle, E1 could merge first and E2 could follow while E3 remains a draft. However, CT400 and physical Vimba acceptance are both pending, and project policy must determine whether physical acceptance is mandatory. I recommend holding both until the responsible instrument owners provide any required acceptance and reviewers sign off. E1/E2 should not be represented as physically qualified.

E3's finalizer correction is now published in #270, whose sole parent is #268 head `fae56c9d1fca574c089e0dacccf9d23e0195a6bb`; its base is the #268 branch. The published tree `b9272cf5…` exactly matched the validated local report/source tree. This preserves PR #268 history and makes #270's current review diff only the follow-up. After #268 merges, retarget #270 to main and verify the diff remains only the follow-up. Keep them in sequence and do not merge #268 without #270's correction/review readiness if that would deploy E3 without the finalizer fix.

## Suggested integration sequence after gates pass

1. Keep #270 based on #268 while the stack is under review. The user has authorized the runner update; publish it as a draft successor after GitHub CLI authentication is restored, then verify its focused diff and fresh CI.
2. Merge #266 (`87791a2…` → `main`) using a merge commit after required CT400 acceptance/review. Fetch again. Retarget #267 (`04cf3f…`) to `main`, verify its diff contains only E2, wait for fresh required CI/reviews, then merge by merge commit after any required Vimba acceptance.
3. Fetch again. Retarget #268 (`fae56c9…`) to `main` and verify its diff contains only E3. Require native MATLAB acceptance and fresh CI/review, then merge by merge commit.
4. After #268 merges, retarget #270 (`f2e0b27…` plus any subsequently authorized runner update) to `main`. Verify its diff contains only the finalizer correction, tests, and approved qualification files. Require fresh CI/review and ensure E3 is not released between the two merges. Merge #270 by merge commit only after these gates pass.
5. Keep all PRs in draft until required acceptance and reviewer sign-off are attached to the exact final SHA. Confirm branch protection, required checks, approvals, and release steps directly in GitHub. No merge is approved by this report.

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

- The user-authorized runner and widget changes remain local because GitHub CLI authentication is invalid and the environment denied the HTTPS login socket.
- Native Engine startup showed Fatal Startup Error and returned `Transport stopped`; its cause and license status remain unknown. Do not repeat startup until the test-owned failed child has exited and the startup error is understood.
- A native MATLAB case times out, fails FIG assertions, returns without orderly Engine disposal, or leaves an unattributed process.
- CT400 or Vimba acceptance is absent for a phase that changes its ownership path, or the operator cannot establish device identity/safe state.
- Any PR's base/head diff is cumulative or conflicts with the expected stage, required CI fails/is absent, or repository branch protection remains unchecked.

No merge, PR retarget, branch update, forced push, hardware call, or MATLAB launch was performed as part of these rehearsals. Temporary rehearsal worktrees and evidence were retained.

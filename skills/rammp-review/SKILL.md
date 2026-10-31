---
name: rammp-review
description: Use when reviewing a RAMMP capability engine PR or branch, or any rammp-org code a student wants merged, against the RAMMP review rules (REVIEW_RULES.md in rammp-deployments).
---

# RAMMP review

Reviews one PR or branch against `REVIEW_RULES.md` and returns a review in the
rules' output format. The rules file is the source of truth for what to check
and how the review reads. This skill only gathers the code and delivers the
result.

## Target

| Argument                                                     | Target             | Base                 |
| ------------------------------------------------------------ | ------------------ | -------------------- |
| PR number or URL (`-R rammp-org/<repo>` if not in that repo) | the PR head        | the PR's base branch |
| branch name                                                  | that branch        | `origin/dev`         |
| nothing                                                      | the current branch | `origin/dev`         |

## Steps

Use one scratch directory for everything: your session scratchpad if you have
one, else `SCRATCH=$(mktemp -d)`.

1. **Rules and manifests.** Clone rammp-deployments `dev`. It holds the rules
   and every deployment manifest (`*/sheppy-manifest.yaml`) the rules check
   names against:

   ```bash
   gh repo clone rammp-org/rammp-deployments "$SCRATCH/deployments" -- --depth 1 --branch dev
   ```

   If that clone has no `REVIEW_RULES.md`, use the one two directories above
   this skill, and say in one line that the rules came from a local copy.

1. **Code.** Get the head into a scratch directory without touching any
   working tree:

   ```bash
   git fetch origin "pull/$N/head"     # PR; for a branch: git fetch origin <branch>
   git worktree add --detach "$SCRATCH/review-$N" FETCH_HEAD
   ```

   Outside a clone of the repo, `gh repo clone rammp-org/<repo> "$SCRATCH/<repo>"`
   first. For a PR, also note the base branch and CI state:
   `gh pr view $N --json baseRefName,url,statusCheckRollup`.

1. **Review.** Dispatch one fresh general-purpose subagent per target, so the
   review is not anchored on this conversation. For several targets, dispatch
   them in parallel. Prompt:

   > Review `<head>` against `<base>` in `<worktree path>` (`git diff <base>...<head>`).
   > Follow `<rules path>` exactly: its How to review steps, its rules, and its Output
   > format. The base branch is `<base>`; CI state is `<passed | failed | not run>`.
   > Deployment manifests are in `$SCRATCH/deployments/*/sheppy-manifest.yaml`.
   > Do not modify files. Return only the review text.

1. **Check the result** before showing it. Every finding has a `path:line`, a
   rule ID or section, one sentence, and a fix where one exists, under the right
   severity. Open the cited line for each must-fix and confirm it says what the
   finding claims. Drop a finding you cannot confirm. Shorten any finding
   longer than two lines.

1. **Clean the wording.** Run the review text through the `unslop` skill's
   rewrite flow if it is installed; otherwise reread each line and cut filler,
   hedges and repeated consequences yourself. Keep the output shape, rule IDs,
   paths and code spans exactly as they are.

1. **Deliver.**

   - Branch: print the review.
   - PR: show the review and ask before posting. On yes:
     `gh pr comment $N -R rammp-org/<repo> --body-file "$SCRATCH/review.md"`.

1. **Clean up:** `git worktree remove "$SCRATCH/review-$N"`.

## Common mistakes

| Mistake                                          | Fix                                                                               |
| ------------------------------------------------ | --------------------------------------------------------------------------------- |
| `gh pr checkout` in the user's working tree      | Use a scratch worktree (step 2).                                                  |
| Reviewing only the diff hunks                    | The rules need the base class, launch file, fragments and Dockerfile around them. |
| Adding a summary, praise, or "overall" paragraph | The output format in the rules is the whole review.                               |
| Flagging what CI enforces                        | The rules list what CI covers; skip it.                                           |

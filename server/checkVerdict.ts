/** A check passes only when it passed. Warning and manual-review rows do not. */

export function checksAllPassed(checks: { passed?: boolean }[] | null | undefined): boolean {
  return (checks || []).every((check) => check.passed === true);
}

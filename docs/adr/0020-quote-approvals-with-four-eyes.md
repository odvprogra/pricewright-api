# 0020. Quote approvals: a request per submission, decided by someone who did not build the quote

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

A quote needs a manager's approval when its value-weighted discount exceeds the tenant's threshold
or a line falls below its margin floor (brief §4, rule 3; decision D-06), and the pricing engine
already lists those reasons (ADR-0004). The brief promised that large discounts would no longer be
approved over chat with no trace, so a decision must record who decided, on what and why. Managers
also build quotes, and from M4 they can set manual overrides.

Practice:

- **Segregation of duties:** one person should not both initiate and approve a sensitive transaction
  ([NIST SP 800-53 AC-5](https://www.stigviewer.com/controls/nist-800-53/AC-5)); in HubSpot's quote
  approvals,
  ["approvers can't approve their own quotes"](https://knowledge.hubspot.com/quotes/manage-quote-approvals).
- **What a decision keeps:** Salesforce keeps the approval history with the approver, the status and
  comments, and lets the submitter
  [recall](https://developer.salesforce.com/docs/atlas.en-us.api_meta.meta/api_meta/meta_approvalprocess.htm)
  a pending request. HubSpot asks the approver to say what to change when asking for changes.
- **Changes after approval:** Salesforce's
  [smart approvals](https://trailhead.salesforce.com/content/learn/modules/advanced-approvals-for-admins/use-smart-approvals-to-evaluate-changes)
  compare what changed with what was approved; SAP
  [resets a release](https://answers.sap.com/questions/7679485/release-strategy---reset.html) when a
  document's value rises after it was released.

## Decision

- **A request per submission.** Submitting a quote that needs approval opens an approval request
  recording who asked, when, the reasons, the discount and threshold, and the list and net
  subtotals, as the quote was priced at submission (ADR-0019). A quote that needs no approval goes
  straight to `approved` and opens none.
- **One pending request at a time**, closed by a decision or withdrawn when the quote is recalled,
  cancelled or revised. Requests are never deleted: a revision's history keeps every request.
- **A decision** records the person, the time and a comment: optional to approve, required to reject
  (what to change), up to 1,000 characters.
- **Four eyes.** The decider is a person (`quotes:approve` is never granted to service accounts) and
  not one of the revision's builders: whoever created it, submitted it, added one of its lines or
  set one of its overrides. Otherwise the decision is refused with `self_approval`, a 403, checked
  after the quote is found because the quote belongs to the caller's own tenant (ADR-0009). A
  manager's own quote therefore needs another manager or an admin.
- **Approved content never changes.** Only drafts change (ADR-0005), so there is nothing to compare
  after approval: a change is a revision, priced again, which asks for approval again if it needs
  it.
- **The inbox** lists pending requests, oldest first; an expired offer's request leaves it, since it
  can only be revised (ADR-0005).

## Alternatives considered

- **Let managers approve their own quotes:** common in small teams, but a manager could set an
  override and approve it; the audit trail would show a decision nobody else took.
- **Exclude only the submitter:** the person who set a 30% override, or added the line below the
  floor, could still approve the result.
- **Re-approval after changes** (smart approvals, release resets): needed when approved documents
  can change; revisions make it unnecessary here.
- **Several approval levels** (larger discounts to more senior people): not in the brief; the
  request already records the discount a level would need.

## Consequences

- **Positive:** every approval names who asked, who decided, on which numbers and why; nobody
  approves their own discount; a property test proves that no quote needing approval is approved,
  sent or accepted without a decision by someone who did not build it.
- **Negative:** a tenant needs at least two people who can approve, or an admin, before a manager's
  own quotes can be approved; a comment is the only explanation of an approval, and it is optional.

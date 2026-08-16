# Solution Steps

1. Make pending-state lookup require both the session ID and student ID so a shared session cannot expose or authorize another student's request.

2. Add a guarded pending-state write for normal runtime use. It may update a request for the same student but must not let another student take over an existing session request.

3. Add an atomic authorization-and-enrollment database operation. Within one transaction, verify the pending owner and course, create the enrollment, and consume the matching pending request.

4. Treat model output only as a proposal. Validate the tool name, turn type, course ID shape, current pending owner, exact pending course, and current user message before authorizing enrollment.

5. Implement conservative application-side message checks: save only explicit first-turn enrollment requests, and accept only clear, unconditional follow-up confirmations. Reject questions, hypotheticals, conditions, hesitation, negation, and confirmations naming a different course.

6. Persist initial valid requests as pending and require a later turn. If a model prematurely proposes enrollment on the initial request, deny the proposal while still requesting separate confirmation.

7. Keep pending state intact after denied confirmations, including shared-device identity mismatches and course mismatches. Clear it only after a matching authorization succeeds.

8. Override unsafe model replies when authorization is denied or completed so the application never repeats an unverified claim that enrollment succeeded.

9. Expand traces to record the model turn type, proposed tool/course, application authorization result, and denial reason separately. Preserve traces for every turn, including malformed and denied proposals.

10. Ground advising context in locally retrieved catalog records, expose only student-scoped pending state to the model, and strengthen the system prompt to distinguish untrusted text, model proposals, and application authority.

11. Retain the OpenAI-backed client and CLI execution path so provider-backed end-to-end checks still exercise the same validated state pipeline.


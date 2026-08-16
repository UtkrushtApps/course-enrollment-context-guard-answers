import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from app.prompts import SYSTEM_POLICY, render_user_context
from app.retrieval import ScenarioRepository, StateStore


class JSONModel(Protocol):
    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Return one structured response from the provider."""


@dataclass(frozen=True)
class TurnResult:
    reply: str
    outcome: str
    course_id: str | None
    pending_course: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "reply": self.reply,
            "outcome": self.outcome,
            "course_id": self.course_id,
            "pending_course": self.pending_course,
        }


_COURSE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,9}-[A-Z0-9]{1,10}$")
_COURSE_IN_TEXT = re.compile(
    r"\b([A-Za-z][A-Za-z0-9]{1,9})\s*-\s*([A-Za-z0-9]{1,10})\b"
)


def _normalize_course_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"\s+", "", value).upper()
    return normalized if _COURSE_ID.fullmatch(normalized) else None


def _courses_in_message(message: str) -> set[str]:
    return {
        f"{match.group(1).upper()}-{match.group(2).upper()}"
        for match in _COURSE_IN_TEXT.finditer(message)
    }


def _is_direct_enrollment_request(message: str, course_id: str) -> bool:
    """Conservatively recognize an explicit first-turn enrollment request."""
    text = message.strip().lower()
    if not text or "?" in text:
        return False
    if re.search(r"\b(if|maybe|might|would|could|hypothetical(?:ly)?)\b", text):
        return False
    if re.search(r"\b(do not|don't|dont|not yet|cancel|stop)\b", text):
        return False
    if not re.search(r"\b(enroll|enrol|register|add)\b", text):
        return False
    return course_id in _courses_in_message(message)


def _is_clear_confirmation(message: str, pending_course: str) -> bool:
    """Independently require an affirmative, unconditional confirmation."""
    text = " ".join(message.strip().lower().split())
    if not text or "?" in text:
        return False
    if re.search(
        r"\b(if|unless|maybe|might|perhaps|depending|provided|assuming|but|however)\b",
        text,
    ):
        return False
    if re.search(
        r"\b(no|not|don't|dont|do not|cancel|stop|wait|hold|unsure|uncertain)\b",
        text,
    ):
        return False

    mentioned_courses = _courses_in_message(message)
    if mentioned_courses and mentioned_courses != {pending_course}:
        return False

    affirmative = (
        r"\b(yes|confirm(?:ed)?|go ahead|proceed|do it|sounds good|"
        r"enroll me|enrol me|register me)\b"
    )
    return re.search(affirmative, text) is not None


def build_messages(
    session_id: str,
    student_id: str,
    message: str,
    repository: ScenarioRepository,
    store: StateStore,
) -> list[dict[str, str]]:
    """Build one turn from student-scoped state and local catalog evidence."""
    pending = store.load_pending(session_id, student_id)
    evidence = repository.retrieve_catalog(message)
    user_context = render_user_context(
        message=message,
        student_id=student_id,
        pending=pending,
        evidence=evidence,
    )
    return [
        {"role": "system", "content": SYSTEM_POLICY},
        {"role": "user", "content": user_context},
    ]


def apply_model_turn(
    session_id: str,
    student_id: str,
    message: str,
    model_output: dict[str, Any],
    store: StateStore,
) -> TurnResult:
    """Validate and apply a model proposal without treating it as authority."""
    pending_before_any = store.load_session_pending(session_id)
    pending_for_student = store.load_pending(session_id, student_id)

    proposed_tool_value = model_output.get("proposed_tool")
    proposed_tool = (
        proposed_tool_value if isinstance(proposed_tool_value, str) else None
    )
    turn_type_value = model_output.get("turn_type")
    turn_type = turn_type_value if isinstance(turn_type_value, str) else None
    course_id = _normalize_course_id(model_output.get("course_id"))
    model_reply = str(
        model_output.get("reply") or "I could not complete that request."
    )

    authorized = False
    authority_reason = "no_tool_proposed"
    outcome = "answered"
    reply = model_reply

    if proposed_tool == "enroll_in_course":
        if course_id is None:
            authority_reason = "invalid_proposed_course"
        elif turn_type != "enrollment_confirmation":
            authority_reason = "not_a_confirmation_turn"
        elif pending_for_student is None:
            authority_reason = "no_matching_student_pending_request"
        elif pending_for_student.course_id != course_id:
            authority_reason = "pending_course_mismatch"
        elif not _is_clear_confirmation(message, pending_for_student.course_id):
            authority_reason = "confirmation_not_clear"
        else:
            decision = store.authorize_and_enroll(
                session_id=session_id,
                student_id=student_id,
                course_id=course_id,
            )
            authorized = decision.authorized
            authority_reason = decision.reason

        if authorized:
            outcome = authority_reason
            if outcome == "enrolled":
                reply = f"Enrollment in {course_id} was completed."
            else:
                reply = f"You were already enrolled in {course_id}."
        else:
            outcome = "authorization_denied"
            reply = (
                "I did not enroll you because there was no matching, clearly "
                "confirmed pending request for this student and course."
            )

    # A first-turn request may establish pending state, but can never authorize
    # enrollment on that same turn. This also handles a model that prematurely
    # proposed the tool by saving the request while keeping the proposal denied.
    if (
        turn_type == "enrollment_request"
        and course_id is not None
        and _is_direct_enrollment_request(message, course_id)
    ):
        saved = store.save_pending_if_available(
            session_id=session_id,
            student_id=student_id,
            course_id=course_id,
        )
        if saved:
            if not authorized:
                outcome = "confirmation_required"
                authority_reason = "pending_request_saved;separate_confirmation_required"
                reply = f"Please clearly confirm that you want to enroll in {course_id}."
        else:
            outcome = "authorization_denied"
            authority_reason = "session_pending_owned_by_another_student"
            reply = (
                "I could not start this enrollment request in the current session. "
                "Please use your own session and try again."
            )

    pending_after_any = store.load_session_pending(session_id)
    pending_after_student = store.load_pending(session_id, student_id)
    detail = json.dumps(
        {
            "model_proposal": {
                "turn_type": turn_type,
                "tool": proposed_tool,
                "course_id": course_id,
            },
            "application_authority": {
                "authorized": authorized,
                "reason": authority_reason,
            },
        },
        sort_keys=True,
    )
    store.write_trace(
        session_id=session_id,
        student_id=student_id,
        message=message,
        proposed_tool=proposed_tool,
        pending_before=(
            pending_before_any.course_id if pending_before_any is not None else None
        ),
        pending_after=(
            pending_after_any.course_id if pending_after_any is not None else None
        ),
        outcome=outcome,
        detail=detail,
        model_turn_type=turn_type,
        proposed_course=course_id,
        authorized=authorized,
    )
    return TurnResult(
        reply=reply,
        outcome=outcome,
        course_id=course_id,
        pending_course=(
            pending_after_student.course_id
            if pending_after_student is not None
            else None
        ),
    )


def run_turn(
    session_id: str,
    student_id: str,
    message: str,
    repository: ScenarioRepository,
    store: StateStore,
    client: JSONModel,
) -> TurnResult:
    messages = build_messages(
        session_id=session_id,
        student_id=student_id,
        message=message,
        repository=repository,
        store=store,
    )
    model_output = client.complete_json(messages)
    return apply_model_turn(
        session_id=session_id,
        student_id=student_id,
        message=message,
        model_output=model_output,
        store=store,
    )

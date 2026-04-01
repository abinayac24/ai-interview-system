from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_repository
from app.email_service import send_report_email_async
from app.models import DomainInterviewStart, InterviewAnswerRequest
from app.services.ai_evaluator import evaluator
from app.services.keyword_matcher import keyword_matcher
from app.services.question_extractor import question_extractor
from app.services.report_generator import report_generator
from app.services.resume_parser import resume_parser
from app.services.voice_handler import voice_handler


router = APIRouter(tags=["interviews"])


@router.post("/interviews/{session_id}/answer")
def submit_answer(
    session_id: str,
    payload: InterviewAnswerRequest,
    repository=Depends(get_repository),
):
    session = repository.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    question = repository.get_current_question(session_id)
    if not question:
        raise HTTPException(status_code=400, detail="No active question")

    normalized_answer = voice_handler.normalize_transcript(payload.answer_text)

    evaluation = evaluator.evaluate_answer(
        question=question["question"],
        answer=normalized_answer,
    )

    repository.record_answer(session_id, question, normalized_answer, evaluation)
    repository.advance_session(session_id)

    next_question = repository.get_current_question(session_id)

    return {
        "session_id": session_id,
        "completed": next_question is None,
        "next_question": next_question,
        "evaluation": evaluation,
    }

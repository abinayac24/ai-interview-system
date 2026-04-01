import { useEffect, useState, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { InterviewPanel } from "../components/InterviewPanel";
import { Shell } from "../components/Shell";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import { useSpeechSynthesis } from "../hooks/useSpeechSynthesis";
import { getInterviewSession, submitInterviewAnswer } from "../lib/api";

export function SessionPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();

  const [session, setSession] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [latestEvaluation, setLatestEvaluation] = useState(null);
  const [timeLeft, setTimeLeft] = useState(30);

  const { transcript, setTranscript, listening, startListening, stopListening, supported } = useSpeechRecognition();
  const { speak } = useSpeechSynthesis();

  const [error, setError] = useState(null);

  const hasSpokenGreetingRef = useRef(false);

  useEffect(() => {
    getInterviewSession(sessionId)
      .then((data) => {
        setSession(data);
        setError(null);
        if (data.greeting && !hasSpokenGreetingRef.current) {
          hasSpokenGreetingRef.current = true;
          speak(`${data.greeting} ${data.question?.question || ""}`, () => {
            startListening(handleSilenceTimeout);
          });
        }
      })
      .catch((err) => {
        console.error("Failed to load session:", err);
        setError("Session not found or expired. Please start a new interview.");
        // Redirect to home after 3 seconds
        setTimeout(() => navigate("/"), 3000);
      });
  }, [sessionId, speak, startListening, navigate]);

  // Timer logic
  useEffect(() => {
    if (!session || !listening) return;

    if (timeLeft <= 0) {
      handleFinalSubmission();
      return;
    }

    const timer = setTimeout(() => {
      setTimeLeft((prev) => prev - 1);
    }, 1000);

    return () => clearTimeout(timer);
  }, [timeLeft, session, listening]);

  const handleSilenceTimeout = () => {
    handleFinalSubmission();
  };

  async function handleFinalSubmission() {
    if (submitting) return;
    setSubmitting(true);
    let finalAnswer = transcript;

    if (listening) {
      finalAnswer = await stopListening();
    }

    await submitAnswer(finalAnswer);
  }

  async function submitAnswer(textToSubmit) {
    if (!textToSubmit || !textToSubmit.trim()) {
      speak("I did not hear an answer. Please repeat your answer.", () => {
        setTimeLeft(30);
        startListening(handleSilenceTimeout);
        setSubmitting(false);
      });
      return;
    }

    try {
      const response = await submitInterviewAnswer(sessionId, textToSubmit);
      setLatestEvaluation(response.evaluation);
      setTranscript("");
      setTimeLeft(30);

      if (response.completed) {
        navigate(`/report/${sessionId}`);
        return;
      }

      const nextSession = await getInterviewSession(sessionId);
      setSession(nextSession);

      speak(nextSession.question?.question || "", () => {
        startListening(handleSilenceTimeout);
        setSubmitting(false);
      });
    } catch (err) {
      console.error("API error", err);
      speak("There was an error saving your answer. Please try again.", () => {
        setTimeLeft(30);
        startListening(handleSilenceTimeout);
        setSubmitting(false);
      });
    }
  }

  if (error) {
    return (
      <Shell>
        <div className="glass-panel rounded-[28px] p-8 text-white text-center">
          <p className="text-red-400 text-xl mb-4">⚠️ {error}</p>
          <p className="text-slate-400">Redirecting to start page...</p>
        </div>
      </Shell>
    );
  }

  if (!session) {
    return (
      <Shell>
        <div className="glass-panel rounded-[28px] p-8 text-white text-center">
          <p>Loading interview session...</p>
          <p className="text-slate-400 text-sm mt-2">Please wait...</p>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      {!supported && (
        <div className="mb-6 rounded-3xl border border-amber-400/25 bg-amber-400/10 px-5 py-4 text-sm text-amber-100">
          Browser speech recognition is not available here. You can still type the answer manually and submit it.
        </div>
      )}

      {/* 🔥 SHOW TIMER */}
      {listening && (
        <div className="mb-4 text-white text-lg font-semibold">
          Time Left to Speak: {timeLeft}s
        </div>
      )}

      {latestEvaluation && (
        <div className="glass-panel mb-6 rounded-[28px] p-6 text-white">
          <p className="section-label text-sm font-semibold">Latest evaluation</p>
          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <Metric title="Score" value={`${latestEvaluation.score}%`} />
            <Metric title="Feedback" value={latestEvaluation.feedback} />
            <Metric title="Suggestion" value={latestEvaluation.improvement_suggestion} />
          </div>
        </div>
      )}

      <InterviewPanel
        session={session}
        transcript={transcript}
        setTranscript={setTranscript}
        listening={listening}
        onSpeakQuestion={() => speak(session.question?.question || "")}
        submitting={submitting}
      />
    </Shell>
  );
}

function Metric({ title, value }) {
  return (
    <div className="metric-tile rounded-3xl p-4">
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">{title}</p>
      <p className="mt-3 text-sm leading-6 text-slate-200">{value}</p>
    </div>
  );
}
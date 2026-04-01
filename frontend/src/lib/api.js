import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000/api"
});

export async function fetchDomains() {
  const response = await api.get("/metadata/domains");
  return response.data.domains;
}

export async function startDomainInterview(payload) {
  const response = await api.post("/interviews/domain/start", payload);
  return response.data;
}

export async function startResumeInterview(formData) {
  const response = await api.post("/interviews/resume/start", formData, {
    headers: { "Content-Type": "multipart/form-data" }
  });
  return response.data;
}

export async function startCompanyInterview(formData) {
  const response = await api.post("/interviews/company/start", formData, {
    headers: { "Content-Type": "multipart/form-data" }
  });
  return response.data;
}

export async function getInterviewSession(sessionId) {
  const response = await api.get(`/interviews/${sessionId}`);
  return response.data;
}

export async function submitInterviewAnswer(sessionId, answerText) {
  const response = await api.post(`/interviews/${sessionId}/answer`, { answer_text: answerText });
  return response.data;
}

export async function getInterviewReport(sessionId) {
  const response = await api.get(`/interviews/${sessionId}/report`);
  return response.data;
}

export function getReportPdfUrl(sessionId) {
  return `${api.defaults.baseURL}/interviews/${sessionId}/report/pdf`;
}

import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";

export function useSpeechRecognition() {
  const recognitionRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  const [transcript, setTranscript] = useState("");
  const [listening, setListening] = useState(false);
  const [supported] = useState(
    Boolean(window.SpeechRecognition || window.webkitSpeechRecognition)
  );

  const silenceTimerRef = useRef(null);
  const onSilenceRef = useRef(null);

  useEffect(() => {
    const SpeechRecognition =
      window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.lang = "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onstart = () => {};

    recognition.onend = () => {
      // Only restart if we're still supposed to be listening and not manually stopped
      if (listening && recognitionRef.current) {
        setTimeout(() => {
          try { 
            recognitionRef.current.start(); 
          } catch (e) {
            console.log("Recognition restart failed (already starting):", e.message);
          }
        }, 100);
      }
    };

    recognition.onerror = (e) => {
      console.error("Speech recognition error:", e.error, e.message);
      // Don't restart on 'no-speech' or 'aborted' errors to avoid loops
      if (e.error === 'not-allowed') {
        console.error("Microphone permission denied");
      }
    };

    recognition.onresult = (event) => {
      let finalTranscript = "";
      let interimTranscript = "";
      
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalTranscript += transcript + " ";
        } else {
          interimTranscript += transcript;
        }
      }

      // Update transcript immediately with interim results for real-time feedback
      if (finalTranscript.trim() || interimTranscript.trim()) {
        setTranscript((prev) => {
          const base = prev.endsWith(interimTranscript.trim()) ? prev : prev + " " + finalTranscript;
          return (base + " " + interimTranscript).trim();
        });
      }
      
      // Only trigger silence detection on final results
      if (finalTranscript.trim()) {
        if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = setTimeout(() => {
           if (onSilenceRef.current) onSilenceRef.current();
        }, 3000);
      }
    };

    recognitionRef.current = recognition;
    return () => {
      recognition.stop();
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);
    };
  }, [listening]);

  const startListening = useCallback(async (onSilenceCallback) => {
    setTranscript("");
    setListening(true);
    onSilenceRef.current = onSilenceCallback;
    
    // Small delay to ensure recognition is ready after state update
    setTimeout(() => {
      if (recognitionRef.current) {
        try { 
          recognitionRef.current.start(); 
          console.log("Speech recognition started");
        } catch (e) {
          console.error("Failed to start recognition:", e.message);
          // If already started, stop and restart
          if (e.message?.includes("already started")) {
            try {
              recognitionRef.current.stop();
              setTimeout(() => recognitionRef.current?.start(), 200);
            } catch (e2) {
              console.error("Recognition restart failed:", e2.message);
            }
          }
        }
      } else {
        console.error("Recognition not initialized");
      }
    }, 50);
    
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mediaRecorder = new MediaRecorder(stream);
        mediaRecorderRef.current = mediaRecorder;
        audioChunksRef.current = [];

        mediaRecorder.ondataavailable = (e) => {
           if (e.data.size > 0) audioChunksRef.current.push(e.data);
        };

        mediaRecorder.start();
        console.log("MediaRecorder started for Whisper backup");
    } catch(err) {
        console.error("Mic permission error:", err);
        alert("Please allow microphone access for voice recognition to work.");
    }
  }, []);

  const stopListening = useCallback(() => {
    return new Promise((resolve) => {
      if (!listening && !mediaRecorderRef.current) return resolve(transcript);
      
      setListening(false);
      if (recognitionRef.current) recognitionRef.current.stop();
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current);

      const recorder = mediaRecorderRef.current;
      if (recorder && recorder.state !== "inactive") {
          recorder.onstop = async () => {
             const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
             const formData = new FormData();
             formData.append("file", audioBlob, "audio.webm");

             try {
               // Add timeout to prevent hanging requests
               const res = await axios.post("http://127.0.0.1:9000/transcribe", formData, {
                 headers: { "Content-Type": "multipart/form-data" },
                 timeout: 10000, // 10 second timeout
               });
               console.log("Whisper response:", res.data);
               if (res.data.text && res.data.text.trim()) {
                   setTranscript(res.data.text.trim());
                   resolve(res.data.text.trim());
               } else {
                   resolve(transcript);
               }
             } catch(err) {
               console.error("Whisper error:", err.message);
               // If Whisper fails, fallback to browser transcript
               if (transcript && transcript.trim()) {
                 console.log("Falling back to browser transcript");
                 resolve(transcript);
               } else {
                 resolve("");
               }
             }
             recorder.stream.getTracks().forEach(t => t.stop());
          };
          recorder.stop();
      } else {
          resolve(transcript);
      }
    });
  }, [listening, transcript]);

  return { transcript, setTranscript, listening, supported, startListening, stopListening };
}
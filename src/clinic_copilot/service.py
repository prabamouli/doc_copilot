from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from clinic_copilot.config import settings
from clinic_copilot.demo_data import demo_cases
from clinic_copilot.llm import LLMClient
from clinic_copilot.prompts import build_scribe_system_prompt
from clinic_copilot.regulatory_vault import regulatory_vault
from clinic_copilot.schemas import (
    CaseRecord,
    ClinicalEntities,
    ClinicalNoteRequest,
    ClinicalNoteResponse,
    ConversationCaptureEntry,
    ExtractedFact,
    NoteAmendmentRequest,
    ReviewFlag,
    ReviewDecisionRequest,
    SoapNote,
    SoapSection,
)
from clinic_copilot.storage import ClinicRepository

os.environ.setdefault("HAYSTACK_TELEMETRY_ENABLED", "false")

try:
    from haystack import Document, Pipeline, component
    from haystack.components.builders import PromptBuilder
    from haystack.components.embedders import SentenceTransformersTextEmbedder
    from haystack.components.generators.chat import OpenAIChatGenerator
    from haystack.components.joiners import DocumentJoiner
    from haystack.components.rankers import SentenceTransformersSimilarityRanker
    from haystack.components.retrievers.in_memory import InMemoryBM25Retriever, InMemoryEmbeddingRetriever
    from haystack.dataclasses import ChatMessage
    from haystack.document_stores.in_memory import InMemoryDocumentStore
    from haystack.utils import Secret

    HAYSTACK_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    HAYSTACK_AVAILABLE = False
    Document = None
    Pipeline = None
    PromptBuilder = None
    OpenAIChatGenerator = None
    SentenceTransformersTextEmbedder = None
    InMemoryBM25Retriever = None
    InMemoryEmbeddingRetriever = None
    DocumentJoiner = None
    SentenceTransformersSimilarityRanker = None
    InMemoryDocumentStore = None
    ChatMessage = None
    Secret = None
    component = None

try:
    import whisper

    WHISPER_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    whisper = None
    WHISPER_AVAILABLE = False

SOAP_JSON_TEMPLATE = """
You are a clinical documentation assistant.
Generate a SOAP note from the transcript below.

Rules:
- Only use information explicitly present in the transcript.
- Do not invent symptoms, diagnoses, medications, tests, or vitals.
- If information is missing, return "unknown" or an empty list.
- Keep the response concise and clinically formatted.
- Return valid JSON only.

Transcript:
{{ transcript }}

Return JSON in exactly this shape:
{
  "summary": "string",
    "claimed_symptoms": ["string"],
  "entities": {
    "symptoms": [{"value": "string", "status": "supported|unknown", "confidence": "low|medium|high"}],
    "duration": [{"value": "string", "status": "supported|unknown", "confidence": "low|medium|high"}],
    "severity": [{"value": "string", "status": "supported|unknown", "confidence": "low|medium|high"}],
    "medical_history": [{"value": "string", "status": "supported|unknown", "confidence": "low|medium|high"}],
    "medications": [{"value": "string", "status": "supported|unknown", "confidence": "low|medium|high"}],
    "allergies": [{"value": "string", "status": "supported|unknown", "confidence": "low|medium|high"}],
    "vitals": [{"value": "string", "status": "supported|unknown", "confidence": "low|medium|high"}]
  },
  "soap_note": {
    "subjective": {"text": "string"},
    "objective": {"text": "string"},
    "assessment": {"text": "string"},
    "plan": {"text": "string"}
  },
  "differential_diagnosis": [],
  "review_flags": [],
  "disclaimer": "Clinician review required."
}
""".strip()


MEDICAL_ENTITY_TEMPLATE = """
You are a clinical entity extractor.
Extract only transcript-supported symptoms and vitals.
Return valid JSON only with this exact schema:
{
    "symptoms": ["string"],
    "vitals": ["string"]
}

Transcript:
{{ transcript }}
""".strip()


LONGITUDINAL_SOAP_TEMPLATE = """
You are a medical scribe generating a longitudinal SOAP note.

Current transcript:
{{ transcript }}

Extracted current entities:
- Symptoms: {{ symptoms }}
- Vitals: {{ vitals }}

Patient historical records (retrieved from pgvector):
{{ historical_records }}

Rules:
- Use only facts supported by current transcript or historical records provided above.
- Every historical statement must include a citation like [Source: Visit <id-or-date>].
- If current transcript contradicts historical records, capture the mismatch in the audit section.
- If details are missing, use "unknown".
- Return valid JSON matching this exact schema:
{
    "summary": "string",
    "entities": {
        "symptoms": [{"value": "string", "status": "supported|unknown|inferred", "confidence": "low|medium|high"}],
        "duration": [{"value": "string", "status": "supported|unknown|inferred", "confidence": "low|medium|high"}],
        "severity": [{"value": "string", "status": "supported|unknown|inferred", "confidence": "low|medium|high"}],
        "medical_history": [{"value": "string", "status": "supported|unknown|inferred", "confidence": "low|medium|high"}],
        "medications": [{"value": "string", "status": "supported|unknown|inferred", "confidence": "low|medium|high"}],
        "allergies": [{"value": "string", "status": "supported|unknown|inferred", "confidence": "low|medium|high"}],
        "vitals": [{"value": "string", "status": "supported|unknown|inferred", "confidence": "low|medium|high"}]
    },
    "soap_note": {
        "subjective": {"text": "string"},
        "objective": {"text": "string"},
        "assessment": {"text": "string"},
        "plan": {"text": "string"}
    },
    "differential_diagnosis": [],
    "audit": [
        {
            "field": "string",
            "current_transcript_value": "string",
            "historical_value": "string",
            "source": "string"
        }
    ],
    "review_flags": [],
    "disclaimer": "Clinician review required."
}
""".strip()


if HAYSTACK_AVAILABLE:

    @component
    class LocalWhisperTranscriberComponent:
        def __init__(self, model_name: str = "base") -> None:
            self._model_name = model_name
            self._model = None

        @component.output_types(transcript=str)
        def run(self, audio_path: str) -> dict[str, str]:
            if not WHISPER_AVAILABLE:
                raise RuntimeError("openai-whisper is not installed")

            path = Path(audio_path)
            if not path.exists() or not path.is_file():
                raise ValueError(f"Audio file not found: {audio_path}")

            if self._model is None:
                self._model = whisper.load_model(self._model_name)

            result = self._model.transcribe(str(path), fp16=False)
            transcript = str(result.get("text", "")).strip()
            if not transcript:
                raise ValueError("Whisper transcriber returned an empty transcript")
            return {"transcript": transcript}


    @component
    class MedicalEntityExtractorComponent:
        """Jinja2-backed prompt component dedicated to symptom and vitals extraction."""

        def __init__(self) -> None:
            self._prompt_builder = PromptBuilder(
                template=MEDICAL_ENTITY_TEMPLATE,
                required_variables=["transcript"],
            )

        @component.output_types(prompt=str)
        def run(self, transcript: str) -> dict[str, str]:
            prompt = self._prompt_builder.run(transcript=transcript)["prompt"]
            return {"prompt": prompt}


    @component
    class EntityProjectionComponent:
        @component.output_types(symptoms=str, vitals=str)
        def run(self, payload: dict) -> dict[str, str]:
            symptoms_raw = payload.get("symptoms", [])
            vitals_raw = payload.get("vitals", [])

            symptoms = [str(item).strip() for item in symptoms_raw if str(item).strip()] if isinstance(symptoms_raw, list) else []
            vitals = [str(item).strip() for item in vitals_raw if str(item).strip()] if isinstance(vitals_raw, list) else []

            return {
                "symptoms": ", ".join(symptoms) if symptoms else "unknown",
                "vitals": ", ".join(vitals) if vitals else "unknown",
            }


    @component
    class PatientHistoryDocumentLoaderComponent:
        def __init__(self, repository: ClinicRepository) -> None:
            self._repository = repository

        @component.output_types(documents=list[Document], query_embedding=list[float], query_text=str, top_k=int)
        def run(self, patient_id: str, current_complaint: str, top_k: int = 5) -> dict[str, Any]:
            chunk_rows = self._repository.list_note_chunks(patient_id=patient_id, limit=1000)
            documents: list[Document] = []
            for row in chunk_rows:
                text_chunk = str(row.get("text_chunk", "")).strip()
                if not text_chunk:
                    continue
                embedding = row.get("embedding")
                if embedding is None:
                    try:
                        embedding = self._repository.embed_query(text_chunk)
                    except Exception:
                        embedding = None
                doc = Document(
                    content=text_chunk,
                    embedding=embedding,
                    meta={
                        "visit_id": str(row.get("visit_id", "unknown-visit")),
                        "date": str(row.get("created_at", "unknown-date")),
                    },
                )
                documents.append(doc)

            query_embedding = self._repository.embed_query(current_complaint)
            return {
                "documents": documents,
                "query_embedding": query_embedding,
                "query_text": current_complaint,
                "top_k": top_k,
            }


    @component
    class HybridRetrieveAndRerankComponent:
        def __init__(self) -> None:
            self._joiner = DocumentJoiner(join_mode="reciprocal_rank_fusion")
            self._reranker = None
            try:
                self._reranker = SentenceTransformersSimilarityRanker(
                    model=settings.history_reranker_model,
                    top_k=max(1, settings.history_candidate_pool_size),
                    scale_score=True,
                )
            except Exception:
                self._reranker = None

        @component.output_types(matches=list[dict])
        def run(
            self,
            documents: list[Document],
            query_embedding: list[float],
            query_text: str,
            top_k: int,
        ) -> dict[str, list[dict[str, Any]]]:
            if not documents:
                return {"matches": []}

            store = InMemoryDocumentStore()
            store.write_documents(documents)

            embedding_retriever = InMemoryEmbeddingRetriever(document_store=store)
            bm25_retriever = InMemoryBM25Retriever(document_store=store)

            candidate_k = max(max(1, int(top_k)) * 2, max(1, settings.history_candidate_pool_size))

            try:
                vector_hits = embedding_retriever.run(query_embedding=query_embedding, top_k=candidate_k)["documents"]
            except Exception:
                vector_hits = []
            keyword_hits = bm25_retriever.run(query=query_text, top_k=candidate_k)["documents"]

            fused = self._joiner.run(documents=[vector_hits, keyword_hits])["documents"]
            candidates = fused[: max(1, settings.history_candidate_pool_size)]
            if self._reranker is not None:
                reranked = self._reranker.run(query=query_text, documents=candidates)["documents"]
            else:
                reranked = candidates

            results: list[dict[str, Any]] = []
            for doc in reranked[: max(1, int(top_k))]:
                meta = doc.meta or {}
                results.append(
                    {
                        "visit_id": str(meta.get("visit_id", "unknown-visit")),
                        "date": str(meta.get("date", "unknown-date")),
                        "score": float(doc.score or 0.0),
                        "source": "hybrid_rrf_reranked",
                        "text_chunk": doc.content or "",
                    }
                )
            return {"matches": results}


    @component
    class HistoricalContextFormatterComponent:
        @component.output_types(historical_context=str)
        def run(self, matches: list[dict]) -> dict[str, str]:
            if not matches:
                return {"historical_context": "No relevant historical context found."}

            lines: list[str] = ["Historical Context:"]
            for idx, match in enumerate(matches[:5], start=1):
                visit_id = str(match.get("visit_id", "unknown-visit"))
                date = str(match.get("date", "unknown-date"))
                score = float(match.get("score", 0.0))
                chunk = str(match.get("text_chunk", "")).strip()
                if not chunk:
                    continue
                lines.append(f"{idx}. Visit={visit_id}; Date={date}; Relevance={score:.3f}; Note={chunk}")

            if len(lines) == 1:
                lines.append("No relevant historical context found.")
            return {"historical_context": "\n".join(lines)}


    @component
    class RetrievedMatchesOutputComponent:
        @component.output_types(retrieved=list[dict])
        def run(self, matches: list[dict]) -> dict[str, list[dict]]:
            return {"retrieved": matches}


    class PatientHistoryRetriever:
        def __init__(self, repository: ClinicRepository) -> None:
            self._repository = repository
            self._pipeline = self._build_pipeline() if HAYSTACK_AVAILABLE else None

        def run(self, patient_id: str, current_complaint: str, top_k: int = 5) -> str:
            payload = self.retrieve(patient_id=patient_id, current_complaint=current_complaint, top_k=top_k)
            return payload["historical_context"]

        def retrieve(self, patient_id: str, current_complaint: str, top_k: int = 5) -> dict[str, Any]:
            if self._pipeline is None:
                raise RuntimeError("Haystack is not installed")

            result = self._pipeline.run(
                data={
                    "history_loader": {
                        "patient_id": patient_id,
                        "current_complaint": current_complaint,
                        "top_k": top_k,
                    }
                }
            )

            historical_context = result.get("historical_formatter", {}).get(
                "historical_context",
                "No relevant historical context found.",
            )
            retrieved = result.get("matches_output", {}).get("retrieved", [])

            return {
                "patient_id": patient_id,
                "current_complaint": current_complaint,
                "historical_context": historical_context,
                "retrieved": retrieved,
            }

        def _build_pipeline(self) -> Pipeline:
            pipe = Pipeline()
            pipe.add_component("history_loader", PatientHistoryDocumentLoaderComponent(self._repository))
            pipe.add_component("hybrid_retrieve", HybridRetrieveAndRerankComponent())
            pipe.add_component("historical_formatter", HistoricalContextFormatterComponent())
            pipe.add_component("matches_output", RetrievedMatchesOutputComponent())
            pipe.connect("history_loader.documents", "hybrid_retrieve.documents")
            pipe.connect("history_loader.query_embedding", "hybrid_retrieve.query_embedding")
            pipe.connect("history_loader.query_text", "hybrid_retrieve.query_text")
            pipe.connect("history_loader.top_k", "hybrid_retrieve.top_k")
            pipe.connect("hybrid_retrieve.matches", "historical_formatter.matches")
            pipe.connect("hybrid_retrieve.matches", "matches_output.matches")
            return pipe


    @component
    class HistoricalRecordRetrieverComponent:
        def __init__(self, retriever: "PatientHistoryRetriever") -> None:
            self._retriever = retriever

        @component.output_types(historical_records=str)
        def run(self, patient_id: str, query_text: str, top_k: int = 5) -> dict[str, str]:
            return {
                "historical_records": self._retriever.run(
                    patient_id=patient_id,
                    current_complaint=query_text,
                    top_k=top_k,
                )
            }


    @component
    class LongitudinalSoapPromptComponent:
        def __init__(self) -> None:
            self._prompt_builder = PromptBuilder(
                template=LONGITUDINAL_SOAP_TEMPLATE,
                required_variables=["transcript", "symptoms", "vitals", "historical_records"],
            )

        @component.output_types(prompt=str)
        def run(
            self,
            transcript: str,
            symptoms: str,
            vitals: str,
            historical_records: str,
        ) -> dict[str, str]:
            return {
                "prompt": self._prompt_builder.run(
                    transcript=transcript,
                    symptoms=symptoms,
                    vitals=vitals,
                    historical_records=historical_records,
                )["prompt"]
            }


    @component
    class LongitudinalJsonValidatorComponent:
        @component.output_types(validated_json=dict)
        def run(self, payload: dict) -> dict[str, dict]:
            _normalize_payload_for_schema(payload)
            validated = ClinicalNoteResponse.model_validate(payload)
            return {"validated_json": validated.model_dump()}

    @component
    class PromptToMessagesComponent:
        @component.output_types(messages=list[ChatMessage])
        def run(self, prompt: str) -> dict[str, list[ChatMessage]]:
            return {
                "messages": [
                    ChatMessage.from_system(
                        "You are a clinical documentation assistant. Return only valid JSON."
                    ),
                    ChatMessage.from_user(prompt),
                ]
            }


    @component
    class ContextAwarePromptToMessagesComponent:
        @component.output_types(messages=list[ChatMessage])
        def run(self, prompt: str, historical_context: str) -> dict[str, list[ChatMessage]]:
            system_prompt = build_scribe_system_prompt(historical_context)
            return {
                "messages": [
                    ChatMessage.from_system(system_prompt),
                    ChatMessage.from_user(prompt),
                ]
            }


    @component
    class ChatRepliesToJsonComponent:
        @component.output_types(payload=dict)
        def run(self, replies: list[ChatMessage]) -> dict[str, dict]:
            if not replies:
                raise ValueError("Generator returned no replies")
            raw_text = replies[0].text.strip()
            return {"payload": json.loads(raw_text)}


    @component
    class HallucinationFilterComponent:
        @component.output_types(validated_json=dict)
        def run(self, transcript: str, payload: dict) -> dict[str, dict]:
            _normalize_payload_for_schema(payload)
            normalized_transcript = _normalize_text(transcript)
            transcript_tokens = set(normalized_transcript.split())
            unsafe_symptoms: list[str] = []

            symptom_items = payload.get("entities", {}).get("symptoms", [])
            safe_symptoms: list[dict[str, Any]] = []
            for item in symptom_items:
                symptom_value = item.get("value", "").strip() if isinstance(item, dict) else ""
                if not symptom_value:
                    continue
                if symptom_value.lower() in {"unknown", "none", "n/a", "na"}:
                    continue
                if _is_transcript_supported(
                    phrase=symptom_value,
                    normalized_transcript=normalized_transcript,
                    transcript_tokens=transcript_tokens,
                ):
                    safe_symptoms.append(item)
                else:
                    unsafe_symptoms.append(symptom_value)

            # LLM may surface additional symptom claims in SOAP prose without adding them to entities.
            claimed_symptoms = payload.get("claimed_symptoms", [])
            if isinstance(claimed_symptoms, list):
                for symptom_value in claimed_symptoms:
                    if not isinstance(symptom_value, str):
                        continue
                    cleaned = symptom_value.strip()
                    if not cleaned or cleaned in unsafe_symptoms:
                        continue
                    if cleaned.lower() in {"unknown", "none", "n/a", "na"}:
                        continue
                    if not _is_transcript_supported(
                        phrase=cleaned,
                        normalized_transcript=normalized_transcript,
                        transcript_tokens=transcript_tokens,
                    ):
                        unsafe_symptoms.append(cleaned)

            payload.setdefault("entities", {})["symptoms"] = safe_symptoms
            payload.setdefault("review_flags", [])

            for unsafe_symptom in unsafe_symptoms:
                payload["review_flags"].append(
                    {
                        "issue": f"Unsupported symptom removed by hallucination filter: {unsafe_symptom}",
                        "severity": "critical",
                        "recommendation": "Verify transcript evidence before reintroducing this symptom.",
                    }
                )
                _scrub_text_fields(payload, unsafe_symptom)

            payload.pop("claimed_symptoms", None)

            payload.setdefault(
                "disclaimer",
                "Clinician review required. This output supports documentation and must not be used as a standalone medical decision.",
            )

            validated = ClinicalNoteResponse.model_validate(payload)
            return {"validated_json": validated.model_dump()}


class HaystackMedicalDocumentationPipeline:
    def __init__(self) -> None:
        self._pipeline = self._build_pipeline() if HAYSTACK_AVAILABLE else None

    @property
    def available(self) -> bool:
        return self._pipeline is not None

    def run(self, transcript: str) -> ClinicalNoteResponse:
        validated_json = self.run_validated_json(transcript)
        return ClinicalNoteResponse.model_validate(validated_json)

    def run_validated_json(self, transcript: str) -> dict[str, Any]:
        if self._pipeline is None:
            raise RuntimeError("Haystack is not installed")

        masked_transcript = regulatory_vault.sanitize_for_llm(
            text=transcript,
            route="haystack.openai_chat_generator",
            metadata={"model": settings.openai_model or settings.ollama_model},
        )

        result = self._pipeline.run(
            data={
                "soap_prompt": {"transcript": masked_transcript},
                "hallucination_filter": {"transcript": transcript},
            }
        )
        return result["hallucination_filter"]["validated_json"]

    def _build_pipeline(self) -> Pipeline:
        generator = OpenAIChatGenerator(
            api_key=Secret.from_token(settings.openai_api_key or "sk-local"),
            api_base_url=settings.openai_base_url or "http://127.0.0.1:4000/v1",
            model=settings.openai_model or settings.ollama_model,
            generation_kwargs={"response_format": {"type": "json_object"}},
        )

        pipe = Pipeline()
        pipe.add_component(
            "soap_prompt",
            PromptBuilder(template=SOAP_JSON_TEMPLATE, required_variables=["transcript"]),
        )
        pipe.add_component("prompt_to_messages", PromptToMessagesComponent())
        pipe.add_component("soap_generator", generator)
        pipe.add_component("json_parser", ChatRepliesToJsonComponent())
        pipe.add_component("hallucination_filter", HallucinationFilterComponent())

        pipe.connect("soap_prompt.prompt", "prompt_to_messages.prompt")
        pipe.connect("prompt_to_messages.messages", "soap_generator.messages")
        pipe.connect("soap_generator.replies", "json_parser.replies")
        pipe.connect("json_parser.payload", "hallucination_filter.payload")
        return pipe


class HaystackLongitudinalScribePipeline:
    def __init__(self, history_retriever: "PatientHistoryRetriever") -> None:
        self._history_retriever = history_retriever
        self._pipeline = self._build_pipeline() if HAYSTACK_AVAILABLE else None

    @property
    def available(self) -> bool:
        return self._pipeline is not None

    def run_from_audio(self, audio_path: str, patient_id: str) -> ClinicalNoteResponse:
        if self._pipeline is None:
            raise RuntimeError("Haystack is not installed")

        result = self._pipeline.run(
            data={
                "audio_transcriber": {"audio_path": audio_path},
                "historical_retriever": {"patient_id": patient_id},
            }
        )
        validated_json = result["longitudinal_validator"]["validated_json"]
        return ClinicalNoteResponse.model_validate(validated_json)

    def _build_pipeline(self) -> Pipeline:
        entity_generator = OpenAIChatGenerator(
            api_key=Secret.from_token(settings.openai_api_key or "sk-local"),
            api_base_url=settings.openai_base_url or "http://127.0.0.1:4000/v1",
            model=settings.openai_model or settings.ollama_model,
            generation_kwargs={"response_format": {"type": "json_object"}},
        )
        longitudinal_generator = OpenAIChatGenerator(
            api_key=Secret.from_token(settings.openai_api_key or "sk-local"),
            api_base_url=settings.openai_base_url or "http://127.0.0.1:4000/v1",
            model=settings.openai_model or settings.ollama_model,
            generation_kwargs={"response_format": {"type": "json_object"}},
        )

        pipe = Pipeline()
        pipe.add_component("audio_transcriber", LocalWhisperTranscriberComponent(model_name=settings.whisper_model))
        pipe.add_component("entity_extractor", MedicalEntityExtractorComponent())
        pipe.add_component("entity_prompt_to_messages", PromptToMessagesComponent())
        pipe.add_component("entity_generator", entity_generator)
        pipe.add_component("entity_json_parser", ChatRepliesToJsonComponent())
        pipe.add_component("entity_projection", EntityProjectionComponent())

        pipe.add_component(
            "historical_retriever",
            HistoricalRecordRetrieverComponent(retriever=self._history_retriever),
        )

        pipe.add_component("longitudinal_prompt", LongitudinalSoapPromptComponent())
        pipe.add_component("longitudinal_prompt_to_messages", ContextAwarePromptToMessagesComponent())
        pipe.add_component("longitudinal_generator", longitudinal_generator)
        pipe.add_component("longitudinal_json_parser", ChatRepliesToJsonComponent())
        pipe.add_component("longitudinal_validator", LongitudinalJsonValidatorComponent())

        pipe.connect("audio_transcriber.transcript", "entity_extractor.transcript")
        pipe.connect("entity_extractor.prompt", "entity_prompt_to_messages.prompt")
        pipe.connect("entity_prompt_to_messages.messages", "entity_generator.messages")
        pipe.connect("entity_generator.replies", "entity_json_parser.replies")
        pipe.connect("entity_json_parser.payload", "entity_projection.payload")

        pipe.connect("audio_transcriber.transcript", "historical_retriever.query_text")
        pipe.connect("audio_transcriber.transcript", "longitudinal_prompt.transcript")
        pipe.connect("entity_projection.symptoms", "longitudinal_prompt.symptoms")
        pipe.connect("entity_projection.vitals", "longitudinal_prompt.vitals")
        pipe.connect("historical_retriever.historical_records", "longitudinal_prompt.historical_records")

        pipe.connect("longitudinal_prompt.prompt", "longitudinal_prompt_to_messages.prompt")
        pipe.connect("historical_retriever.historical_records", "longitudinal_prompt_to_messages.historical_context")
        pipe.connect("longitudinal_prompt_to_messages.messages", "longitudinal_generator.messages")
        pipe.connect("longitudinal_generator.replies", "longitudinal_json_parser.replies")
        pipe.connect("longitudinal_json_parser.payload", "longitudinal_validator.payload")
        return pipe


class LocalNliEntailmentScorer:
    def __init__(self) -> None:
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._entailment_index = 2
        self._contradiction_index = 0
        self._load_error: str | None = None

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._model is not None and self._tokenizer is not None and self._torch is not None

    @property
    def load_error(self) -> str:
        self._ensure_loaded()
        return self._load_error or "NLI model unavailable"

    def score_max(self, premises: list[str], hypothesis: str) -> tuple[float, float]:
        if not premises:
            return (0.0, 0.0)
        if not self.available:
            return (0.0, 0.0)

        entailment_scores: list[float] = []
        contradiction_scores: list[float] = []
        batch_size = 6

        for index in range(0, len(premises), batch_size):
            batch = premises[index : index + batch_size]
            encoded = self._tokenizer(
                batch,
                [hypothesis] * len(batch),
                truncation=True,
                max_length=384,
                padding=True,
                return_tensors="pt",
            )

            with self._torch.no_grad():
                logits = self._model(**encoded).logits
                probs = self._torch.softmax(logits, dim=-1)

            for row in probs:
                entailment_scores.append(float(row[self._entailment_index]))
                contradiction_scores.append(float(row[self._contradiction_index]))

        return (max(entailment_scores, default=0.0), max(contradiction_scores, default=0.0))

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if self._load_error is not None:
            return

        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except Exception as exc:
            self._load_error = str(exc)
            return

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                settings.nli_model,
                local_files_only=not settings.allow_remote_model_downloads,
            )
            self._model = AutoModelForSequenceClassification.from_pretrained(
                settings.nli_model,
                local_files_only=not settings.allow_remote_model_downloads,
            )
            self._model.eval()
            self._torch = torch

            id2label = {int(idx): str(label).lower() for idx, label in self._model.config.id2label.items()}
            for idx, label in id2label.items():
                if "entail" in label:
                    self._entailment_index = idx
                if "contrad" in label:
                    self._contradiction_index = idx
        except Exception as exc:
            self._load_error = str(exc)


class RagNliHallucinationGuardrail:
    def __init__(self, repository: ClinicRepository) -> None:
        self._repository = repository
        self._nli = LocalNliEntailmentScorer()

    def evaluate(
        self,
        note: ClinicalNoteResponse,
        transcript: str,
        patient_id: str | None,
        current_complaint: str,
    ) -> list[ReviewFlag]:
        claims = _extract_note_claims(note)
        if not claims:
            return []

        transcript_chunks = _split_semantic_chunks(transcript)
        if not transcript_chunks:
            return [
                ReviewFlag(
                    issue="Guardrail skipped due to empty transcript context",
                    severity="warning",
                    recommendation="Ensure transcript chunks are available for hallucination checks.",
                )
            ]

        if not self._nli.available:
            return [
                ReviewFlag(
                    issue=f"NLI guardrail unavailable: {self._nli.load_error}",
                    severity="warning",
                    recommendation="Install and cache the configured DeBERTa model for entailment checks.",
                )
            ]

        flags: list[ReviewFlag] = []
        for claim in claims[:24]:
            evidence = _retrieve_relevant_evidence_chunks(claim, transcript_chunks, top_k=4)

            if patient_id:
                try:
                    historical = self._repository.search_note_chunks(
                        patient_id=patient_id,
                        query_text=f"{current_complaint}\n{claim}",
                        top_k=2,
                    )
                    evidence.extend(
                        str(item.get("text_chunk", "")).strip()
                        for item in historical
                        if str(item.get("text_chunk", "")).strip()
                    )
                except Exception:
                    pass

            entailment, contradiction = self._nli.score_max(evidence, claim)
            if entailment >= settings.nli_entailment_threshold:
                continue

            severity = "critical" if contradiction >= settings.nli_contradiction_threshold else "warning"
            flags.append(
                ReviewFlag(
                    issue=(
                        "Potential hallucination: unsupported SOAP claim "
                        f"'{claim[:140]}' (entail={entailment:.2f}, contradict={contradiction:.2f})"
                    ),
                    severity=severity,
                    recommendation="Confirm evidence in transcript or remove unsupported statements before final sign-off.",
                )
            )

        return flags


class ClinicalDocumentationService:
    def __init__(self, llm_client: LLMClient, repository: ClinicRepository) -> None:
        self._llm_client = llm_client
        self._repository = repository
        self._patient_history_retriever = PatientHistoryRetriever(repository) if HAYSTACK_AVAILABLE else None
        self._haystack_pipeline = HaystackMedicalDocumentationPipeline()
        self._longitudinal_pipeline = (
            HaystackLongitudinalScribePipeline(self._patient_history_retriever)
            if self._patient_history_retriever
            else None
        )
        self._hallucination_guardrail = RagNliHallucinationGuardrail(repository)

    def generate_note(self, request: ClinicalNoteRequest) -> CaseRecord:
        response = self._generate_note_response(request)
        response.review_flags.extend(self._local_review_flags(request))
        response.review_flags.extend(
            self._hallucination_guardrail.evaluate(
                note=response,
                transcript=request.transcript,
                patient_id=None,
                current_complaint=request.transcript,
            )
        )
        return self._repository.create_case(request, response)

    def generate_note_with_haystack(self, request: ClinicalNoteRequest) -> ClinicalNoteResponse:
        response = self._haystack_pipeline.run(request.transcript)
        response.review_flags.extend(self._local_review_flags(request))
        response.review_flags.extend(
            self._hallucination_guardrail.evaluate(
                note=response,
                transcript=request.transcript,
                patient_id=None,
                current_complaint=request.transcript,
            )
        )
        return response

    def generate_longitudinal_note_from_audio(self, audio_path: str, patient_id: str) -> ClinicalNoteResponse:
        if self._longitudinal_pipeline is None or not self._longitudinal_pipeline.available:
            raise RuntimeError("Haystack is not installed")

        audio_file = Path(audio_path)
        if not audio_file.exists() or not audio_file.is_file():
            raise ValueError(f"Audio file not found: {audio_path}")

        if not WHISPER_AVAILABLE:
            raise RuntimeError("openai-whisper is not installed")

        response = self._longitudinal_pipeline.run_from_audio(audio_path=audio_path, patient_id=patient_id)
        return response

    def seed_demo_case(self) -> CaseRecord:
        seeded_cases = self.seed_demo_cases()
        return seeded_cases[0]

    def seed_demo_cases(self) -> list[CaseRecord]:
        return self._repository.seed_demo_cases(demo_cases())

    def list_cases(self) -> list[CaseRecord]:
        cases = self._repository.list_cases()
        if cases:
            return cases
        return self.seed_demo_cases()

    def get_case(self, case_id: str) -> CaseRecord:
        return self._repository.get_case(case_id)

    def review_case(self, case_id: str, review: ReviewDecisionRequest) -> CaseRecord:
        return self._repository.review_case(case_id, review)

    def amend_case(self, case_id: str, amendment: NoteAmendmentRequest) -> CaseRecord:
        return self._repository.amend_case(case_id, amendment)

    def audit_logs(self, case_id: str | None = None) -> list:
        return self._repository.list_audit_logs(case_id=case_id)

    def capture_conversation_snapshot(self, case_id: str, transcript: str) -> int:
        self._repository.get_case(case_id)
        captures = _extract_conversation_turns(transcript)
        recent_entries = self._repository.list_conversation_captures(case_id=case_id, limit=40)
        optimized = _optimize_periodic_captures(captures=captures, recent_entries=recent_entries)
        return self._repository.capture_conversation(case_id=case_id, captures=optimized)

    def list_conversation_captures(self, case_id: str, limit: int = 100) -> list[ConversationCaptureEntry]:
        self._repository.get_case(case_id)
        return self._repository.list_conversation_captures(case_id=case_id, limit=limit)

    def debug_retrieve_patient_history(self, patient_id: str, current_complaint: str, top_k: int = 5) -> dict[str, Any]:
        if not patient_id.strip():
            raise ValueError("patient_id is required")
        if not current_complaint.strip():
            raise ValueError("current_complaint is required")
        if self._patient_history_retriever is None:
            raise RuntimeError("Haystack is not installed")

        return self._patient_history_retriever.retrieve(
            patient_id=patient_id.strip(),
            current_complaint=current_complaint.strip(),
            top_k=max(1, int(top_k)),
        )

    def _generate_note_response(self, request: ClinicalNoteRequest) -> ClinicalNoteResponse:
        if self._haystack_pipeline.available:
            try:
                return self._haystack_pipeline.run(request.transcript)
            except Exception:
                pass
        return self._llm_client.generate_clinical_note(request)

    def _local_review_flags(self, request: ClinicalNoteRequest) -> list[ReviewFlag]:
        lowered = request.transcript.lower()
        flags: list[ReviewFlag] = []

        if "allerg" not in lowered:
            flags.append(
                ReviewFlag(
                    issue="Allergy status not clearly documented",
                    severity="warning",
                    recommendation="Confirm allergies before finalizing the visit note.",
                )
            )

        if "dose" in lowered and "mg" not in lowered:
            flags.append(
                ReviewFlag(
                    issue="Medication mentioned without clear dosage",
                    severity="warning",
                    recommendation="Verify medication strength and schedule with the clinician.",
                )
            )

        return flags


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\s]+", " ", value.lower()).strip()


def _extract_conversation_turns(transcript: str) -> list[tuple[str, str]]:
    captures: list[tuple[str, str]] = []
    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("doctor:"):
            captures.append(("doctor", line.split(":", 1)[1].strip() or "..."))
            continue
        if lowered.startswith("patient:"):
            captures.append(("patient", line.split(":", 1)[1].strip() or "..."))
            continue
        captures.append(("unknown", line))

    if captures:
        return captures

    chunked = [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", transcript) if chunk.strip()]
    return [("unknown", chunk) for chunk in chunked[:12]]


def _optimize_periodic_captures(
    captures: list[tuple[str, str]],
    recent_entries: list[ConversationCaptureEntry],
) -> list[tuple[str, str]]:
    if not captures:
        return []

    recent_keys = {
        (entry.speaker, _normalize_text(entry.text))
        for entry in recent_entries
    }

    optimized: list[tuple[str, str]] = []
    for speaker, text in captures:
        cleaned = " ".join(text.split())
        if len(cleaned) < 3:
            continue

        key = (speaker, _normalize_text(cleaned))
        if key in recent_keys:
            continue

        # Avoid repeated adjacent snippets in the same periodic tick.
        if optimized:
            prev_speaker, prev_text = optimized[-1]
            if prev_speaker == speaker and _normalize_text(prev_text) == key[1]:
                continue

        optimized.append((speaker, cleaned))
        recent_keys.add(key)

    return optimized


def _extract_note_claims(note: ClinicalNoteResponse) -> list[str]:
    segments = [
        note.summary,
        note.soap_note.subjective.text,
        note.soap_note.objective.text,
        note.soap_note.assessment.text,
        note.soap_note.plan.text,
    ]

    claims: list[str] = []
    for segment in segments:
        if not segment:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", segment):
            cleaned = " ".join(sentence.split()).strip()
            token_count = len(cleaned.split())
            if len(cleaned) < 12 or token_count < 3:
                continue
            claims.append(cleaned)

    for facts in (
        note.entities.symptoms,
        note.entities.medications,
        note.entities.allergies,
        note.entities.vitals,
        note.entities.medical_history,
    ):
        for fact in facts:
            value = fact.value.strip()
            if value and value.lower() not in {"unknown", "none", "n/a", "na"}:
                token_count = len(value.split())
                if token_count < 2 and not re.search(r"\d", value):
                    continue
                claims.append(value)

    deduped: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        key = _normalize_text(claim)
        if key and key not in seen:
            deduped.append(claim)
            seen.add(key)
    return deduped


def _split_semantic_chunks(text: str, max_chars: int = 300) -> list[str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []

    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", cleaned) if segment.strip()]
    if not sentences:
        return [cleaned[:max_chars]]

    chunks: list[str] = []
    buffer: list[str] = []
    current_size = 0
    for sentence in sentences:
        size = len(sentence)
        if buffer and current_size + size + 1 > max_chars:
            chunks.append(" ".join(buffer))
            buffer = [buffer[-1]]
            current_size = len(buffer[0])

        buffer.append(sentence)
        current_size += size + 1

    if buffer:
        chunks.append(" ".join(buffer))

    return chunks[:40]


def _retrieve_relevant_evidence_chunks(claim: str, chunks: list[str], top_k: int = 4) -> list[str]:
    claim_tokens = set(_normalize_text(claim).split())
    if not claim_tokens:
        return chunks[:top_k]

    scored: list[tuple[int, int, str]] = []
    for chunk in chunks:
        chunk_tokens = set(_normalize_text(chunk).split())
        overlap = len(claim_tokens.intersection(chunk_tokens))
        scored.append((overlap, len(chunk_tokens), chunk))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[: max(1, top_k)] if item[2].strip()]


def _is_transcript_supported(
    phrase: str,
    normalized_transcript: str,
    transcript_tokens: set[str],
) -> bool:
    normalized_phrase = _normalize_text(phrase)
    if not normalized_phrase:
        return False
    if normalized_phrase in normalized_transcript:
        return True
    phrase_tokens = set(normalized_phrase.split())
    return bool(phrase_tokens) and phrase_tokens.issubset(transcript_tokens)


def _rank_documents_for_query(documents: list[Document], query_text: str) -> list[Document]:
    query_tokens = set(_normalize_text(query_text).split())
    if not query_tokens:
        return documents

    def score(doc: Document) -> tuple[int, int]:
        content = _normalize_text(doc.content or "")
        tokens = set(content.split())
        overlap = len(tokens.intersection(query_tokens))
        return (overlap, len(content))

    return sorted(documents, key=score, reverse=True)


def _scrub_text_fields(payload: dict[str, Any], phrase: str) -> None:
    pattern = re.compile(re.escape(phrase), re.IGNORECASE)

    def scrub(text: str) -> str:
        cleaned = pattern.sub("", text)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.;")
        return cleaned or "unknown"

    if "summary" in payload and isinstance(payload["summary"], str):
        payload["summary"] = scrub(payload["summary"])

    soap_note = payload.get("soap_note", {})
    if not isinstance(soap_note, dict):
        return

    for section_name in ("subjective", "objective", "assessment", "plan"):
        section = soap_note.get(section_name)
        if isinstance(section, dict) and isinstance(section.get("text"), str):
            section["text"] = scrub(section["text"])


def _normalize_payload_for_schema(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("summary"), str):
        payload["summary"] = str(payload.get("summary", "unknown"))
    payload["summary"] = payload["summary"].strip() or "unknown"

    entities = payload.get("entities")
    if not isinstance(entities, dict):
        payload["entities"] = {}
        entities = payload["entities"]

    for key in (
        "symptoms",
        "duration",
        "severity",
        "medical_history",
        "medications",
        "allergies",
        "vitals",
    ):
        items = entities.get(key, [])
        if not isinstance(items, list):
            entities[key] = []
            continue
        entities[key] = [_normalize_fact_item(item) for item in items]

    soap_note = payload.get("soap_note")
    if not isinstance(soap_note, dict):
        soap_note = {}
        payload["soap_note"] = soap_note

    for section_name in ("subjective", "objective", "assessment", "plan"):
        section = soap_note.get(section_name)
        if isinstance(section, dict):
            text = section.get("text", "unknown")
        elif isinstance(section, str):
            text = section
        else:
            text = "unknown"
        soap_note[section_name] = {
            "text": str(text).strip() or "unknown",
            "evidence": [],
        }


def _normalize_fact_item(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        value = item.strip()
        return {
            "value": value,
            "status": "supported" if value else "unknown",
            "confidence": "medium",
            "evidence": [],
        }

    if not isinstance(item, dict):
        return {"value": "unknown", "status": "unknown", "confidence": "low", "evidence": []}

    value = str(item.get("value", "")).strip() or "unknown"
    status = str(item.get("status", "supported")).strip().lower()
    confidence = str(item.get("confidence", "medium")).strip().lower()

    if status not in {"supported", "unknown", "inferred"}:
        status = "supported" if value != "unknown" else "unknown"
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"

    evidence = item.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []

    return {
        "value": value,
        "status": status,
        "confidence": confidence,
        "evidence": evidence,
    }

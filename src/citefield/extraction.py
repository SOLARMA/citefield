"""LLM calls, response validation and retries."""

from __future__ import annotations

import uuid
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from pydantic import ValidationError

from citefield.config import Config, FieldDef
from citefield.llm.base import Completion, LLMBackend, Message
from citefield.merge import Draft, field_status, merge
from citefield.output import (
    DocumentInfo,
    ExtractionResult,
    FieldResult,
    OutputWarning,
    QuoteEvidence,
    RunInfo,
    TokenUsage,
)
from citefield.parsers import get_parser, parse_document
from citefield.parsers.base import DocumentParser, ParsedDocument
from citefield.planning import Plan, make_plan
from citefield.prompts import extract_v1
from citefield.verify import PARSED_TYPES, evidence_match, parse_value


class ExtractionError(Exception):
    """Extraction can't start: missing settings or unsupported options."""


class Extractor:
    """Runs extraction with the parser and LLM backend named in the config's settings."""

    def __init__(
        self,
        config: Config,
        *,
        parser: DocumentParser | None = None,
        backend: LLMBackend | None = None,
    ) -> None:
        self.config = config
        self._parser = parser
        self._backend = backend

    @property
    def parser(self) -> DocumentParser:
        if self._parser is None:
            self._parser = get_parser(self.config.settings.parser)
        return self._parser

    @property
    def backend(self) -> LLMBackend:
        if self._backend is None:
            from citefield.llm.litellm import LiteLLMBackend

            llm = self.config.settings.llm
            if not llm.model:
                raise ExtractionError("settings.llm.model is not set")
            self._backend = LiteLLMBackend(
                llm.model,
                llm.temperature,
                llm.max_retries,
                reasoning_effort=llm.reasoning_effort,
                provider=llm.provider,
            )
        return self._backend

    def parse(self, path: str | Path) -> ParsedDocument:
        """Parse a document; raises `citefield.parsers.ParseError` if it can't."""
        return parse_document(path, self.parser)

    def plan(self, document: ParsedDocument) -> Plan:
        settings = self.config.settings
        if settings.extraction.n_runs != 1:
            raise ExtractionError("n_runs > 1 is not supported yet")
        return make_plan(
            document,
            self.config.fields,
            self.backend,
            chunking=settings.chunking,
            batch_size=settings.extraction.field_batch_size,
            n_runs=settings.extraction.n_runs,
        )

    def extract(self, path: str | Path, *, document_id: str | None = None) -> ExtractionResult:
        started_at = datetime.now().astimezone()
        document = self.parse(path)
        plan = self.plan(document)
        settings = self.config.settings
        pages = {u.locator.page: u.text for u in document.units if u.locator.page is not None}
        warnings = _document_warnings(document, self.config.fields)

        tokens = TokenUsage()
        calls = 0
        drafts: dict[str, list[Draft]] = {f.name: [] for f in self.config.fields}
        errors: dict[str, str] = {}
        for batch in plan.batches:
            completion, attempts, error = self._complete(batch.messages)
            calls += attempts
            if completion is None:
                errors.update((f.name, error[:1000]) for f in batch.fields)
                continue
            tokens.input += completion.input_tokens or 0
            tokens.output += completion.output_tokens or 0

            by_name = {f.name: f for f in batch.fields}
            for answer in completion.parsed.fields:
                field = by_name.get(answer.name)
                if field is None:
                    warnings.append(OutputWarning(code="unexpected_field", raw=answer.name))
                    continue
                for candidate in answer.candidates:
                    draft = _draft(field, candidate, pages, self.config, warnings)
                    if draft is not None:
                        drafts[field.name].append(draft)

        min_ratio = settings.verification.fuzzy_min_ratio
        fields = []
        for field in self.config.fields:
            if field.name in errors:
                fields.append(
                    FieldResult(name=field.name, status="error", error=errors[field.name])
                )
                continue
            values = merge(field.name, drafts[field.name])
            for value in values:
                if value.signals.evidence_match < min_ratio:
                    warnings.append(
                        OutputWarning(
                            code="evidence_not_verified",
                            field=field.name,
                            raw=value.raw_value,
                            message=f"best evidence_match {value.signals.evidence_match} "
                            f"is below fuzzy_min_ratio {min_ratio}",
                        )
                    )
            fields.append(FieldResult(name=field.name, status=field_status(values), values=values))

        return ExtractionResult(
            citefield_version=version("citefield"),
            document=DocumentInfo(
                document_id=document_id or Path(document.path).stem,
                path=document.path,
                sha256=document.sha256,
                units=len(document.units),
                parser=document.parser,
            ),
            run=RunInfo(
                run_id=uuid.uuid4().hex,
                started_at=started_at,
                model=settings.llm.model or self.backend.name,
                prompt_version=extract_v1.VERSION,
                schema_hash=self.config.schema_hash(),
                settings_hash=self.config.settings_hash(),
                n_runs=plan.n_runs,
                chunks=plan.chunks,
                llm_calls=calls,
                tokens=tokens,
            ),
            fields=fields,
            warnings=warnings,
        )

    def reextract(self, path: str | Path, previous: str | Path) -> ExtractionResult:
        """Re-run extraction and carry reviews over from a previous output."""
        raise NotImplementedError

    def _complete(self, messages: list[Message]) -> tuple[Completion | None, int, str]:
        """Call the backend, retrying invalid responses up to `max_retries` times.

        Returns the completion (None on failure), the number of attempts and the last error.
        """
        retries = self.config.settings.llm.max_retries
        error = "no response"
        for attempt in range(1, retries + 2):
            try:
                completion = self.backend.complete_structured(messages, extract_v1.ExtractResponse)
            except ValidationError as exc:
                error = f"invalid response after {attempt} attempt(s): {exc}"
            except Exception as exc:  # API errors; the backend already retried them
                return None, attempt, f"{type(exc).__name__}: {exc}"
            else:
                return completion, attempt, ""
        return None, retries + 1, error


def _document_warnings(document: ParsedDocument, fields: list[FieldDef]) -> list[OutputWarning]:
    warnings = []
    low = [u.locator.page for u in document.low_text_units if u.locator.page is not None]
    if low:
        warnings.append(OutputWarning(code="low_text_units", units=low))
    for field in fields:
        if field.type not in PARSED_TYPES:
            warnings.append(
                OutputWarning(
                    code="unsupported_field_type",
                    field=field.name,
                    message=f"{field.type} values are not parsed yet; value stays null",
                )
            )
    return warnings


def _draft(
    field: FieldDef,
    candidate: extract_v1.Candidate,
    pages: dict[int, str],
    config: Config,
    warnings: list[OutputWarning],
) -> Draft | None:
    """Turn one LLM candidate into a verified draft, or None if it has no usable evidence."""
    raw_value = candidate.raw_value.strip()
    evidence = [
        QuoteEvidence(page=e.page, quote=e.quote.strip())
        for e in candidate.evidence
        if e.quote.strip()
    ]
    if not raw_value or not evidence:
        return None  # a candidate without evidence is not a value (ADR-0003)

    settings = config.settings
    raw_unit = (candidate.raw_unit or "").strip() or None
    parsed = parse_value(
        field, raw_value, raw_unit, settings.extraction.number_locale, config.units
    )
    if parsed.warning is not None:
        warnings.append(parsed.warning)
    return Draft(
        raw_value=raw_value,
        raw_unit=raw_unit,
        value=parsed.value,
        status=parsed.status,
        unit=parsed.unit,
        derivation=parsed.derivation,
        unit_resolved=parsed.unit_resolved,
        context=(candidate.context or "").strip() or None,
        evidence=evidence,
        matches=[
            evidence_match(e.quote, e.page, pages, page_window=settings.verification.page_window)
            for e in evidence
        ],
        model_confidence=min(max(candidate.model_confidence, 0.0), 1.0),
    )

"""Lightweight attachment references plus keyword-based recall."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Sequence

from app.config import config
from app.services.attachment_context_service import AttachmentDocument

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,}|[\u4e00-\u9fff]{2,}")
_CONTEXT_BLOCK_RE = re.compile(
    r"\[附件 (?P<file_name>[^\]]+)\]\s*"
    r"(?:\nfile_id: (?P<file_id>[^\n]+)\s*)?"
    r"(?:\nstatus: (?P<status>[^\n]*)\s*)?"
    r"\ncontent:\n(?P<content>.*?)(?=\n\n\[附件 |\Z)",
    re.S,
)
_GENERIC_FILE_TERMS = {
    "附件",
    "文件",
    "文档",
    "资料",
    "这个文件",
    "那个文件",
    "pdf",
    "doc",
    "docx",
    "txt",
    "md",
    "markdown",
    "log",
    "日志",
    "表格",
    "csv",
    "json",
}
_STOPWORDS = {
    "这个",
    "那个",
    "这个文件",
    "那个文件",
    "一个",
    "一种",
    "我们",
    "你们",
    "请问",
    "帮我",
    "继续",
    "然后",
    "里面",
    "内容",
    "什么",
    "怎么",
    "哪些",
    "相关",
    "情况",
}
_OVERVIEW_HINTS = ("讲了什么", "主要内容", "主要讲", "概要", "概述", "摘要", "重点", "总结")
_DETAIL_HINTS = ("详细", "具体", "原文", "全文", "第", "章节", "页", "提到", "怎么说", "步骤", "摘录")


@dataclass(frozen=True, slots=True)
class AttachmentReference:
    file_id: str
    file_name: str
    summary: str
    keywords: tuple[str, ...]
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "file_name": self.file_name,
            "summary": self.summary,
            "keywords": list(self.keywords),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttachmentReference | None":
        file_id = str(data.get("file_id") or "").strip()
        file_name = str(data.get("file_name") or "").strip()
        summary = str(data.get("summary") or "").strip()
        if not file_id or not file_name:
            return None
        normalized_keywords: list[str] = []
        for token in data.get("keywords") or []:
            normalized = _normalize_token(str(token))
            if normalized:
                normalized_keywords.append(normalized)
        return cls(
            file_id=file_id,
            file_name=file_name,
            summary=summary,
            keywords=tuple(normalized_keywords),
            status=str(data.get("status") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class ResolvedAttachmentReference:
    reference: AttachmentReference
    score: int
    matched_keywords: tuple[str, ...]


class AttachmentReferenceService:
    def __init__(
        self,
        summary_max_chars: int | None = None,
        keyword_limit: int | None = None,
    ) -> None:
        self.summary_max_chars = (
            summary_max_chars
            if summary_max_chars is not None
            else int(getattr(config, "harness_attachment_summary_max_chars", 600))
        )
        self.keyword_limit = (
            keyword_limit
            if keyword_limit is not None
            else int(getattr(config, "harness_attachment_keyword_limit", 12))
        )

    def build_references(
        self, documents: Sequence[AttachmentDocument]
    ) -> list[AttachmentReference]:
        references: list[AttachmentReference] = []
        for document in documents:
            references.append(
                self._make_reference(
                    file_id=document.file_id,
                    file_name=document.file_name,
                    status=document.status,
                    content=document.content,
                )
            )
        return references

    def build_references_from_context(
        self,
        context: str,
        fallback_file_ids: Sequence[str] | None = None,
    ) -> list[AttachmentReference]:
        references: list[AttachmentReference] = []
        fallback_ids = list(fallback_file_ids or [])
        for index, match in enumerate(_CONTEXT_BLOCK_RE.finditer(context or "")):
            file_name = str(match.group("file_name") or "").strip()
            file_id = str(match.group("file_id") or "").strip()
            if not file_id and index < len(fallback_ids):
                file_id = str(fallback_ids[index] or "").strip()
            if not file_name or not file_id:
                continue
            references.append(
                self._make_reference(
                    file_id=file_id,
                    file_name=file_name,
                    status=str(match.group("status") or "").strip(),
                    content=str(match.group("content") or "").strip(),
                )
            )
        return references

    def build_summary_context(self, references: Sequence[AttachmentReference]) -> str:
        sections: list[str] = []
        for reference in references:
            keywords = ", ".join(reference.keywords)
            sections.append(
                (
                    f"[附件摘要 {reference.file_name}]"
                    f"\nfile_id: {reference.file_id}"
                    f"\nstatus: {reference.status}"
                    f"\nsummary:\n{reference.summary}"
                    f"\nkeywords: {keywords}"
                ).strip()
            )
        return "\n\n".join(section for section in sections if section.strip())

    def build_active_index(self, references: Sequence[AttachmentReference]) -> str:
        """Render a compact ``file_id -> summary -> keywords`` index.

        Used by the harness system prompt so the model knows which files
        are available in the session and can request full-content reload
        via ``attachment_refs`` (file_id) when it needs details.

        Returning an empty string means there are no attachments in scope.
        """
        deduped: dict[str, AttachmentReference] = {}
        for reference in references:
            existing = deduped.get(reference.file_id)
            if existing is None:
                deduped[reference.file_id] = reference
                continue
            # Keep the richest entry when the same file appears in multiple turns.
            if len(reference.summary) > len(existing.summary):
                deduped[reference.file_id] = reference
        if not deduped:
            return ""
        lines: list[str] = []
        for reference in deduped.values():
            keywords = ", ".join(reference.keywords)
            status = f" (status={reference.status})" if reference.status else ""
            summary = reference.summary or "（无摘要）"
            lines.append(
                f"- file_id={reference.file_id}{status} | 文件名={reference.file_name} | "
                f"summary={summary} | keywords={keywords}"
            )
        return "\n".join(lines)

    def format_turn_refs_for_summary(
        self, turn: dict[str, Any]
    ) -> str:
        """Render a single turn's ``attachment_refs`` as a one-line hint.

        Used as an extra hint fed to the rolling-summary LLM so the summary
        keeps a lightweight reminder of which files were attached in each
        historic turn. We deliberately emit only the file_id + filename +
        keywords to keep the summary input budget low.
        """
        references: list[AttachmentReference] = []
        for raw_ref in turn.get("attachment_refs") or []:
            if not isinstance(raw_ref, dict):
                continue
            reference = AttachmentReference.from_dict(raw_ref)
            if reference is not None:
                references.append(reference)
        if not references:
            return ""
        parts: list[str] = []
        for reference in references:
            keywords = ", ".join(reference.keywords) if reference.keywords else "—"
            parts.append(f"{reference.file_name}({reference.file_id}; keywords={keywords})")
        return "本轮附件：" + "; ".join(parts)

    def resolve_reference(
        self,
        question: str,
        turns: Sequence[dict[str, Any]],
    ) -> ResolvedAttachmentReference | None:
        candidates = self._collect_candidates(turns)
        if not candidates:
            return None

        normalized_question = (question or "").strip()
        question_keywords = self._question_keywords(normalized_question)
        mentions_file = self._mentions_file(normalized_question)
        best: ResolvedAttachmentReference | None = None

        for recency_rank, reference in enumerate(candidates):
            matched_keywords = self._matched_keywords(reference, question_keywords, normalized_question)
            score = 0
            if matched_keywords:
                score += len(matched_keywords) * 10
            if reference.file_id and reference.file_id.lower() in normalized_question.lower():
                score += 100
            if reference.file_name and reference.file_name.lower() in normalized_question.lower():
                score += 80
            if mentions_file:
                score += max(1, 5 - recency_rank)

            resolved = ResolvedAttachmentReference(
                reference=reference,
                score=score,
                matched_keywords=matched_keywords,
            )
            if best is None or resolved.score > best.score:
                best = resolved

        if best is None:
            return None
        if best.matched_keywords:
            return best
        if mentions_file and len(candidates) == 1:
            return best
        if mentions_file and best.score >= 5:
            return best
        return None

    def should_reload_full_content(
        self,
        question: str,
        resolved: ResolvedAttachmentReference,
    ) -> bool:
        normalized_question = (question or "").strip()
        if any(hint in normalized_question for hint in _DETAIL_HINTS):
            return True
        if any(hint in normalized_question for hint in _OVERVIEW_HINTS):
            return False
        return bool(resolved.matched_keywords)

    def _make_reference(
        self,
        *,
        file_id: str,
        file_name: str,
        status: str,
        content: str,
    ) -> AttachmentReference:
        summary = self._build_summary(content)
        keywords = self._extract_keywords(file_name, summary, content)
        return AttachmentReference(
            file_id=file_id,
            file_name=file_name,
            summary=summary,
            keywords=keywords,
            status=status,
        )

    def _collect_candidates(
        self, turns: Sequence[dict[str, Any]]
    ) -> list[AttachmentReference]:
        deduped: dict[str, AttachmentReference] = {}
        ordered_ids: list[str] = []
        for turn in reversed(list(turns)):
            refs = turn.get("attachment_refs") or []
            for raw_ref in refs:
                if not isinstance(raw_ref, dict):
                    continue
                reference = AttachmentReference.from_dict(raw_ref)
                if reference is None or reference.file_id in deduped:
                    continue
                deduped[reference.file_id] = reference
                ordered_ids.append(reference.file_id)
        return [deduped[file_id] for file_id in ordered_ids]

    def _matched_keywords(
        self,
        reference: AttachmentReference,
        question_keywords: set[str],
        question: str,
    ) -> tuple[str, ...]:
        matched: list[str] = []
        reference_terms = {
            _normalize_token(reference.file_id),
            *_tokenize(Path(reference.file_name).stem),
            *_tokenize(reference.file_name),
            *reference.keywords,
        }
        for keyword in sorted(question_keywords):
            if keyword in reference_terms:
                matched.append(keyword)
                continue
            if keyword and keyword in question and keyword in reference.summary:
                matched.append(keyword)
        return tuple(dict.fromkeys(matched))

    def _question_keywords(self, question: str) -> set[str]:
        return {
            token
            for token in _tokenize(question)
            if token not in _STOPWORDS and token not in _GENERIC_FILE_TERMS
        }

    @staticmethod
    def _mentions_file(question: str) -> bool:
        normalized = (question or "").lower()
        return any(term.lower() in normalized for term in _GENERIC_FILE_TERMS)

    def _build_summary(self, content: str) -> str:
        lines = [" ".join(line.split()) for line in (content or "").splitlines()]
        selected: list[str] = []
        current_length = 0
        for line in lines:
            if not line:
                continue
            addition = len(line) + (1 if selected else 0)
            if current_length + addition > self.summary_max_chars:
                remaining = max(0, self.summary_max_chars - current_length)
                if remaining > 8:
                    selected.append(line[:remaining].rstrip())
                break
            selected.append(line)
            current_length += addition
            if len(selected) >= 6:
                break
        summary = "\n".join(selected).strip()
        if not summary:
            compact = " ".join((content or "").split())
            return compact[: self.summary_max_chars].strip()
        return summary

    def _extract_keywords(
        self,
        file_name: str,
        summary: str,
        content: str,
    ) -> tuple[str, ...]:
        keyword_pool = list(_tokenize(Path(file_name).stem))
        keyword_pool.extend(_tokenize(file_name))
        keyword_pool.extend(_tokenize(summary))
        keyword_pool.extend(_tokenize(content[: max(1200, self.summary_max_chars * 2)]))
        counter = Counter(
            token
            for token in keyword_pool
            if token not in _STOPWORDS and token not in _GENERIC_FILE_TERMS
        )
        ranked = sorted(counter.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
        return tuple(token for token, _count in ranked[: self.keyword_limit])


def _normalize_token(token: str) -> str:
    return " ".join((token or "").strip().lower().split())


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    tokens: list[str] = []
    for match in _TOKEN_RE.findall(text):
        token = _normalize_token(match)
        if not token or token.isdigit():
            continue
        tokens.append(token)
    return tokens


attachment_reference_service = AttachmentReferenceService()

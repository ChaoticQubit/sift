"""QUARANTINED reader agent (Agno). Touches raw untrusted captions/links. AGENTS.md §2.

NO secrets. NO side-effecting tools. Tools = read/parse only (ocr_images, transcribe_audio,
fetch_link, captions). Emits a TYPED structured record — never free-form text used as instructions.
pre_hooks = [PromptInjectionGuardrail, PIIDetectionGuardrail(mask=True), PresidioGuardrail,
             UntrustedContentGuardrail].
"""
from __future__ import annotations

from ..config import Settings
from ..models import Extraction, Item


def read(item: Item, settings: Settings) -> Extraction:
    """Run the quarantined reader agent over `item` -> sanitized Extraction.

    TODO F0.5/F3: build the Agno Agent with read-only tools + guardrail pre_hooks; route by medium
    (caption first, then images_ocr / reels_stt / links); return typed Extraction.
    """
    raise NotImplementedError("F3: quarantined reader agent")

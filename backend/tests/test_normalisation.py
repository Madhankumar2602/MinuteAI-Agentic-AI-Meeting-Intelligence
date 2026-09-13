"""Unit tests for the deterministic half of the pipeline: grounding and normalisation.

No database, no HTTP, no LLM. These run in milliseconds and pin down the rules
that decide what gets stored.
"""

import uuid
from datetime import date

from app.db.models import ActionItemPriority
from app.schemas.extraction import ExtractedActionItem, ExtractedDecision, MeetingExtraction
from app.services.grounding import TranscriptIndex
from app.services.intelligence import match_owner, normalise_extraction, parse_deadline
from tests.fakes import PLATFORM_SYNC, platform_sync_extraction

MEETING_DAY = date(2026, 9, 10)

# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


def test_exact_quote_is_supported() -> None:
    index = TranscriptIndex(PLATFORM_SYNC)
    assert index.supports("Yes, I'll have the runbook ready by next Wednesday.")


def test_quote_differing_only_in_case_and_punctuation_is_supported() -> None:
    index = TranscriptIndex(PLATFORM_SYNC)
    assert index.supports("YES   ill have the RUNBOOK ready -- by next wednesday")


def test_quote_with_one_word_dropped_is_supported() -> None:
    index = TranscriptIndex(PLATFORM_SYNC)
    # "the" removed from "fix the clock synchronisation on the API servers"
    assert index.supports("please fix clock synchronisation on the API servers")


def test_paraphrased_quote_is_not_supported() -> None:
    index = TranscriptIndex(PLATFORM_SYNC)
    assert not index.supports(
        "Meera committed to repairing the time settings on our backend machines"
    )


def test_invented_quote_is_not_supported() -> None:
    index = TranscriptIndex(PLATFORM_SYNC)
    assert not index.supports("We decided to rewrite the whole frontend in Rust next quarter")


def test_very_short_quote_is_not_accepted_as_evidence() -> None:
    """'Agreed.' appears in the transcript, but proves nothing about which item."""
    index = TranscriptIndex(PLATFORM_SYNC)
    assert not index.supports("Agreed.")


def test_missing_quote_is_not_supported() -> None:
    assert not TranscriptIndex(PLATFORM_SYNC).supports(None)


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


def test_valid_iso_deadline_is_parsed() -> None:
    warnings: list[str] = []
    assert parse_deadline("2026-09-16", MEETING_DAY, warnings) == date(2026, 9, 16)
    assert warnings == []


def test_unparseable_deadline_becomes_none_with_warning() -> None:
    warnings: list[str] = []
    assert parse_deadline("next Friday", MEETING_DAY, warnings) is None
    assert "unparseable" in warnings[0]


def test_implausible_deadline_is_discarded() -> None:
    """A wrong-year resolution must not silently become a real deadline."""
    warnings: list[str] = []
    assert parse_deadline("2016-09-16", MEETING_DAY, warnings) is None
    assert "implausible" in warnings[0]


# ---------------------------------------------------------------------------
# Owner matching
# ---------------------------------------------------------------------------


def test_owner_matches_exact_name_case_insensitively() -> None:
    pid = uuid.uuid4()
    assert match_owner("PRIYA  sharma", {"priya sharma": pid}) == pid


def test_single_first_name_matches_a_unique_full_name() -> None:
    pid = uuid.uuid4()
    assert match_owner("Priya", {"priya sharma": pid, "arjun rao": uuid.uuid4()}) == pid


def test_ambiguous_first_name_is_left_unlinked() -> None:
    """Two Priyas: guessing would assign the task to the wrong person."""
    participants = {"priya sharma": uuid.uuid4(), "priya nair": uuid.uuid4()}
    assert match_owner("Priya", participants) is None


def test_unknown_owner_is_left_unlinked() -> None:
    assert match_owner("Zoe", {"priya sharma": uuid.uuid4()}) is None


# ---------------------------------------------------------------------------
# Whole-extraction normalisation
# ---------------------------------------------------------------------------


def test_normalisation_of_correct_extraction_verifies_all_evidence() -> None:
    result = normalise_extraction(
        platform_sync_extraction(), transcript=PLATFORM_SYNC, meeting_date=MEETING_DAY
    )
    assert len(result.decisions) == 2
    assert len(result.action_items) == 3
    assert all(d.evidence_verified for d in result.decisions)
    assert all(a.evidence_verified for a in result.action_items)
    assert result.action_items[1].priority is ActionItemPriority.HIGH
    assert result.warnings == []


def test_bad_fields_are_dropped_individually_not_the_whole_result() -> None:
    extraction = MeetingExtraction(
        summary="  A   summary  ",
        key_points=["  ", "Real point"],
        participants=["Priya", "priya", " PRIYA "],
        decisions=[
            ExtractedDecision(decision="   ", context=None, evidence_quote=None),
            ExtractedDecision(
                decision="Ship it", context=None, evidence_quote="made up quote that is not there"
            ),
        ],
        action_items=[
            ExtractedActionItem(
                task="Write docs",
                owner="Deepa",  # not listed as a participant
                deadline="sometime",
                deadline_text="sometime",
                priority=None,
                evidence_quote=None,
            ),
            ExtractedActionItem(
                task="",
                owner=None,
                deadline=None,
                deadline_text=None,
                priority=None,
                evidence_quote=None,
            ),
        ],
    )
    result = normalise_extraction(extraction, transcript=PLATFORM_SYNC, meeting_date=MEETING_DAY)

    assert result.summary == "A summary"
    assert result.key_points == ["Real point"]
    # Case-insensitive de-duplication, plus the owner the model forgot to list.
    assert result.participants == ["Priya", "Deepa"]
    # Blank decision and blank task dropped; the rest survives.
    assert [d.decision_text for d in result.decisions] == ["Ship it"]
    assert [a.task for a in result.action_items] == ["Write docs"]
    assert result.action_items[0].deadline is None
    assert result.decisions[0].evidence_verified is False
    assert any("unparseable" in w for w in result.warnings)
    assert any("evidence not found" in w for w in result.warnings)

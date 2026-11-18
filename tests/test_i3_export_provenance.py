"""I3 end-to-end: export preserves source → segment → artifact → transcript → labels → gold → split."""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from apps.core.storage import get_storage
from apps.processing.models import ProcessingJob
from apps.processing.services.job import JobService
from apps.workspace.services import (
    AnnotationService,
    ExportService,
    GoldService,
    LabelService,
    MembershipService,
    SegmentService,
)
from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_i3_end_to_end_export_provenance(project, user, annotator, ready_source):
    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )

    segments = SegmentService()
    segment = segments.create(
        source_id=ready_source.id,
        start_seconds=1.0,
        end_seconds=4.0,
        actor_user_id=user.id,
        transcript="",
    )
    # Bound edit bumps revision; subsequent publish must use the new revision.
    segment = segments.update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        start_seconds=2.0,
        end_seconds=5.0,
    )
    assert segment.definition_revision == 2

    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    payload = b"RIFF" + b"\x00" * 120 + b"WAVE"
    recorded = hashlib.sha256(payload).hexdigest()
    storage_key = (
        f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    )
    jobs.storage.put(storage_key, payload)
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=len(payload),
        checksum=recorded,
    )
    assert published.status == ProcessingJob.Status.SUCCEEDED
    artifact = published.artifact
    assert artifact.checksum == recorded

    accepted = "hello from accepted transcript"
    segment = segments.update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        transcript=accepted,
    )

    label = LabelService().create(
        project_id=project.id, name="Speech", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label.id, actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label.id, actor_user_id=annotator.id
    )
    GoldService().set_labels(
        segment_id=segment.id,
        label_ids=[label.id],
        actor_user_id=user.id,
    )

    snap = ExportService().build_snapshot(
        project.id,
        actor_user_id=user.id,
        split_strategy="by_source",
        split_seed=42,
        split_train=0.8,
        split_val=0.1,
        split_test=0.1,
        supervision_policy="gold",
    )
    row = next(r for r in snap["segments"] if r["segment_id"] == str(segment.id))

    assert row["source_id"] == str(ready_source.id)
    assert row["youtube_video_id"] == ready_source.youtube_video_id
    assert row["start_seconds"] == 2.0
    assert row["end_seconds"] == 5.0
    assert row["definition_revision"] == segment.definition_revision
    assert row["transcript"] == accepted
    assert row["current_artifact"]["checksum"] == recorded
    assert row["current_artifact"]["artifact_id"] == str(artifact.id)
    assert {a["annotator_id"] for a in row["annotations"] if a["removed_at"] is None} == {
        str(user.id),
        str(annotator.id),
    }
    assert row["gold_labels"] == ["Speech"]
    assert row["supervised_labels"] == ["Speech"]
    assert snap["supervision_policy"] == "gold"
    assert row["split"] in {"train", "val", "test"}
    assert snap["split"]["strategy"] == "by_source"

    zip_bytes, _meta = ExportService().render(snap, "dataset_zip")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        audio_names = [n for n in archive.namelist() if n.startswith("audio/") and n.endswith(".wav")]
        assert len(audio_names) == 1
        wav_bytes = archive.read(audio_names[0])
        assert hashlib.sha256(wav_bytes).hexdigest() == recorded
        assert hashlib.sha256(wav_bytes).hexdigest() == artifact.checksum

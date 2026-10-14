"""Video source domain service."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import IntegrityError, transaction

from apps.core.errors import NotFoundError, SourceDuplicateError, ValidationError
from apps.core.storage import get_storage
from apps.core.youtube import extract_youtube_video_id
from apps.processing.models import AsrRun, ProcessingArtifact, SourceMediaCache
from apps.workspace.models import AudioSegment, VideoSource
from apps.workspace.services.membership import MembershipService


def video_description(metadata_json: dict[str, Any] | None) -> str:
    if not isinstance(metadata_json, dict):
        return ""
    value = metadata_json.get("description")
    if not isinstance(value, str):
        return ""
    return value


class SourceService:
    def __init__(self) -> None:
        self.memberships = MembershipService()

    def import_source(
        self,
        *,
        project_id: uuid.UUID,
        youtube_url: str,
        actor_user_id: uuid.UUID,
    ) -> VideoSource:
        self.memberships.require_owner(project_id, actor_user_id)
        self.memberships.require_active_project(project_id)

        video_id = extract_youtube_video_id(youtube_url)
        existing = VideoSource.objects.filter(
            project_id=project_id,
            youtube_video_id=video_id,
        ).first()
        if existing is not None:
            raise SourceDuplicateError(
                "Source already exists for this YouTube video in the project",
                details={
                    "project_id": str(project_id),
                    "youtube_video_id": video_id,
                    "source_id": str(existing.id),
                },
            )

        try:
            with transaction.atomic():
                source = VideoSource.objects.create(
                    project_id=project_id,
                    youtube_video_id=video_id,
                    youtube_url=youtube_url.strip(),
                    source_status=VideoSource.Status.PENDING_METADATA,
                )
        except IntegrityError as exc:
            raise SourceDuplicateError(
                "Source already exists for this YouTube video in the project",
                details={"project_id": str(project_id), "youtube_video_id": video_id},
            ) from exc
        return source

    def get(self, source_id: uuid.UUID) -> VideoSource:
        try:
            return VideoSource.objects.get(pk=source_id)
        except VideoSource.DoesNotExist as exc:
            raise NotFoundError(
                "Source not found",
                details={"source_id": str(source_id)},
            ) from exc

    def get_for_project(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> VideoSource:
        source = self.get(source_id)
        if source.project_id != project_id:
            raise NotFoundError(
                "Source not found",
                details={"source_id": str(source_id)},
            )
        return source

    def list_for_project(self, project_id: uuid.UUID) -> list[VideoSource]:
        return list(
            VideoSource.objects.filter(project_id=project_id).order_by("-created_at")
        )

    def delete_source(
        self,
        source_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> None:
        """Remove a source and its segments. The same YouTube video can be imported again."""
        storage_keys: list[str] = []
        with transaction.atomic():
            try:
                source = VideoSource.objects.select_for_update().get(pk=source_id)
            except VideoSource.DoesNotExist as exc:
                raise NotFoundError(
                    "Source not found",
                    details={"source_id": str(source_id)},
                ) from exc
            self.memberships.require_owner(source.project_id, actor_user_id)
            self.memberships.require_active_project(source.project_id)

            from apps.workspace.services.segment import SegmentService

            segments = SegmentService()
            live_ids = list(
                AudioSegment.objects.filter(
                    source_id=source.id,
                    deleted_at__isnull=True,
                ).values_list("id", flat=True)
            )
            for segment_id in live_ids:
                segments.soft_delete(segment_id, actor_user_id=actor_user_id)

            try:
                storage_keys.append(source.media_cache.storage_key)
            except SourceMediaCache.DoesNotExist:
                pass
            storage_keys.extend(
                ProcessingArtifact.objects.filter(job__source_id=source.id).values_list(
                    "storage_key", flat=True
                )
            )
            # AsrRun restricts deletion of its job and artifact; drop runs first.
            AsrRun.objects.filter(source_id=source.id).delete()
            source.delete()

        storage = get_storage()
        for key in storage_keys:
            if key and storage.exists(key):
                storage.delete(key)

    def update_display_metadata(
        self,
        source_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        title: str | None | object = ...,
        channel_name: str | None | object = ...,
    ) -> VideoSource:
        source = self.get(source_id)
        self.memberships.require_owner(source.project_id, actor_user_id)
        self.memberships.require_active_project(source.project_id)

        if title is not ...:
            source.title = title  # type: ignore[assignment]
        if channel_name is not ...:
            source.channel_name = channel_name  # type: ignore[assignment]
        source.save()
        return source

    def apply_metadata_result(
        self,
        source_id: uuid.UUID,
        *,
        success: bool,
        title: str | None = None,
        channel_name: str | None = None,
        duration_seconds: float | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> VideoSource:
        source = self.get(source_id)
        if success:
            if duration_seconds is not None and duration_seconds <= 0:
                raise ValidationError(
                    "duration_seconds must be positive when provided",
                    details={"fields": [{"field": "duration_seconds", "code": "INVALID"}]},
                )
            source.source_status = VideoSource.Status.READY
            if title is not None:
                source.title = title
            if channel_name is not None:
                source.channel_name = channel_name
            if duration_seconds is not None:
                source.duration_seconds = duration_seconds
            if metadata_json is not None:
                source.metadata_json = metadata_json
        else:
            source.source_status = VideoSource.Status.METADATA_FAILED
            if metadata_json is not None:
                source.metadata_json = metadata_json
        source.save()
        return source

    def request_metadata_refresh(
        self,
        source_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> VideoSource:
        source = self.get(source_id)
        self.memberships.require_owner(source.project_id, actor_user_id)
        self.memberships.require_active_project(source.project_id)
        source.source_status = VideoSource.Status.PENDING_METADATA
        source.save(update_fields=["source_status", "updated_at"])
        return source

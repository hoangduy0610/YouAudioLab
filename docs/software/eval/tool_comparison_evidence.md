# Tool Comparison Evidence for Table 1

This document provides the evidence basis for the capability codes used in Table 1 of the YouAudioLab manuscript.

## Scope and interpretation

The comparison is limited to documented capabilities of the examined software versions. It is not an exhaustive capability audit. A feature coded `U` is **not claimed to be impossible** through custom extensions, scripts, plugins, or undocumented workflows.

### Capability codes

- **B** — Built-in: directly provided and documented by the software/framework.
- **C** — Configurable: representable/configurable within the software, but not necessarily enforced as a dedicated end-to-end workflow.
- **E** — External integration: documented through external tools/scripts rather than as a native workflow.
- **U** — Not identified in the reviewed documentation/repository; not necessarily unsupported.

### Verification methods

- **Official documentation review**
- **Repository inspection**
- **Published software paper**
- **Regression/acceptance evidence** (YouAudioLab)

No independent execution of ELAN, Label Studio, or AudioSet-Tools is claimed here; their entries are based on documentation/repository review.

## Evidence matrix

| Software | Version | Feature | Code | Verification method | Reviewed source | Evidence / rationale |
|---|---|---|---:|---|---|---|
| ELAN | 7.1 | Open source | B | Official distribution inspection | <https://archive.mpi.nl/tla/elan/download> | Official download page provides “Source code of ELAN 7.1” with source files and a Maven build script. |
| ELAN | 7.1 | Built-in YouTube media download | U | Official documentation review | <https://www.mpi.nl/tools/elan/docs/manual/Sec_Clip_Media_Using_Script.html> | Docs cover clipping already-available media through externally configured command-line tools; no built-in YouTube acquisition workflow was identified. |
| ELAN | 7.1 | Revision-aware extraction | U | Official documentation review | <https://www.mpi.nl/tools/elan/docs/manual/> | No stored segment-definition revision that invalidates an in-flight extraction/publication was identified. |
| ELAN | 7.1 | Checksummed audio artifacts | U | Official documentation review | <https://www.mpi.nl/tools/elan/docs/manual/Sec_Export_as.html> | No documented workflow was identified that stores a cryptographic checksum for each extracted audio artifact. |
| ELAN | 7.1 | Verified audio in dataset export | U | Official documentation review | <https://www.mpi.nl/tools/elan/docs/manual/Sec_Export_as.html> | No documented export-time re-hashing/rejection mechanism for changed audio bytes was identified. |
| ELAN | 7.1 | Per-annotator annotation records | C | Official documentation review | <https://www.mpi.nl/tools/elan/docs/manual/Sec_Adding_new_tiers.html> | Tiers include an optional Annotator field with creator name/code; this is configurable tier-level attribution rather than a dedicated per-annotation user-record workflow. |
| ELAN | 7.1 | Source-video-level dataset splitting | U | Official documentation review | <https://www.mpi.nl/tools/elan/docs/manual/> | No built-in train/validation/test partitioner grouping samples by source video with a reproducible seed was identified. |
| ELAN | 7.1 | Taxonomy-aware filtering and class balancing | U | Official documentation review | <https://www.mpi.nl/tools/elan/docs/manual/> | Controlled vocabularies exist, but no taxonomy-aware corpus filtering plus class-rebalancing workflow was identified. |
| Label Studio | CE 1.23.2 | Open source | B | Repository inspection | <https://github.com/HumanSignal/label-studio> | Public repository identifies Label Studio as open source and Apache-2.0 licensed. |
| Label Studio | CE 1.23.2 | Built-in YouTube media download | U | Official documentation review | <https://labelstud.io/guide/tasks> | Docs support files, URLs, APIs and storage imports; no built-in YouTube downloader or yt-dlp acquisition workflow was identified. |
| Label Studio | CE 1.23.2 | Revision-aware extraction | U | Official documentation review | <https://labelstud.io/guide/api> | No segment-definition revision tied to asynchronous media extraction that rejects stale publication was identified. |
| Label Studio | CE 1.23.2 | Checksummed audio artifacts | U | Official documentation review | <https://labelstud.io/guide/export> | No documented per-audio-artifact SHA-256 recording/verification contract was identified. |
| Label Studio | CE 1.23.2 | Verified audio in dataset export | U | Official documentation review | <https://labelstud.io/guide/export> | Exports tasks/annotations in several formats; no export-time re-hashing and rejection of changed source audio bytes was identified. |
| Label Studio | CE 1.23.2 | Per-annotator annotation records | B | Official documentation review | <https://labelstud.io/guide/export> | Raw JSON annotations contain `annotations.completed_by`, documented as the user ID that created the annotation. |
| Label Studio | CE 1.23.2 | Source-video-level dataset splitting | U | Official documentation review | <https://labelstud.io/guide/tasks> | No built-in train/validation/test assignment grouped by source-video identity with a fixed seed was identified. |
| Label Studio | CE 1.23.2 | Taxonomy-aware filtering and class balancing | U | Official documentation review | <https://labelstud.io/guide/labeling> | Configurable labels/interfaces are supported, but no built-in taxonomy-aware dataset filtering plus class-rebalancing pipeline was identified. |
| AudioSet-Tools | 0.1.0 | Open source | B | Repository inspection | <https://github.com/StefanoGiacomelli/audioset-tools> | Public repository distributes the framework source code and states GPL licensing. |
| AudioSet-Tools | 0.1.0 | Built-in YouTube media download | B | Published software paper + repository inspection | <https://link.springer.com/article/10.1186/s13636-025-00436-z> | Paper documents automated segment download/pre-processing; repository includes `audioset_tools/downloaders.py` and download scripts. |
| AudioSet-Tools | 0.1.0 | Revision-aware extraction | U | Published software paper + repository inspection | <https://link.springer.com/article/10.1186/s13636-025-00436-z> | No persisted segment revision with stale asynchronous-job publication checks was identified. |
| AudioSet-Tools | 0.1.0 | Checksummed audio artifacts | U | Published software paper + repository inspection | <https://link.springer.com/article/10.1186/s13636-025-00436-z> | No documented artifact-level SHA-256 integrity contract comparable to YouAudioLab was identified. |
| AudioSet-Tools | 0.1.0 | Verified audio in dataset export | U | Published software paper + repository inspection | <https://link.springer.com/article/10.1186/s13636-025-00436-z> | No documented audio-inclusive export step that re-hashes stored audio and rejects modified/missing files was identified. |
| AudioSet-Tools | 0.1.0 | Per-annotator annotation records | U | Published software paper + repository inspection | <https://link.springer.com/article/10.1186/s13636-025-00436-z> | Framework is an automated AudioSet curation pipeline; no human per-annotator record model was identified. |
| AudioSet-Tools | 0.1.0 | Source-video-level dataset splitting | U | Published software paper + repository inspection | <https://link.springer.com/article/10.1186/s13636-025-00436-z> | Paper describes deterministic metadata slicing and existing AudioSet splits, but no new source-video-grouped train/validation/test assignment was identified. |
| AudioSet-Tools | 0.1.0 | Taxonomy-aware filtering and class balancing | B | Published software paper + repository inspection | <https://link.springer.com/article/10.1186/s13636-025-00436-z> | Taxonomy-consistent filtering and class rebalancing are core documented capabilities, including `Rebalancing_filter()`. |
| YouAudioLab | 0.1.1 | Open source | B | Repository inspection | <https://github.com/ndung768/YouAudioLab> | Public source repository; verify the final immutable release retains the stated Apache-2.0 license. |
| YouAudioLab | 0.1.1 | Built-in YouTube media download | B | Regression/acceptance evidence | <https://github.com/ndung768/YouAudioLab/blob/v0.1.1/docs/software/eval/live_acceptance.md> | Versioned evaluation material documents URL registration/media retrieval in the integrated workflow. Update link if final tag changes. |
| YouAudioLab | 0.1.1 | Revision-aware extraction | B | Regression evidence | <https://github.com/ndung768/YouAudioLab/blob/v0.1.1/tests/test_jobs_races.py> | Regression tests cover revision/stale-publication behavior. Verify final test paths after pending code changes. |
| YouAudioLab | 0.1.1 | Checksummed audio artifacts | B | Regression evidence | <https://github.com/ndung768/YouAudioLab/blob/v0.1.1/tests/test_publish_integrity.py> | Integrity tests support checksum/size/completeness checks before verified publication. Verify final implementation/test paths. |
| YouAudioLab | 0.1.1 | Verified audio in dataset export | B | Regression evidence + repository inspection | <https://github.com/ndung768/YouAudioLab/blob/v0.1.1/apps/workspace/services/export_dataset.py> | Export is documented to re-check checksum/size and reject changed/missing audio; verify final implementation/tests. |
| YouAudioLab | 0.1.1 | Per-annotator annotation records | B | Regression evidence | <https://github.com/ndung768/YouAudioLab/blob/v0.1.1/tests/test_qc1_blind_iaa.py> | Tests support annotator-specific annotation isolation/retention. Verify final release path. |
| YouAudioLab | 0.1.1 | Source-video-level dataset splitting | B | Regression evidence | <https://github.com/ndung768/YouAudioLab/blob/v0.1.1/tests/test_i3_export_provenance.py> | Export provenance tests support source-video-level partitioning and preservation of split assignments. Verify final release path. |
| YouAudioLab | 0.1.1 | Taxonomy-aware filtering and class balancing | U | Repository/documentation inspection | <https://github.com/ndung768/YouAudioLab> | Project-specific labels/supervision exist, but no AudioSet-style taxonomy-aware corpus filtering plus automatic class rebalancing was identified. |
